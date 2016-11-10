import logging
import argparse
import can
import collections
import enum
import epyqlib.busproxy
import epyqlib.canneo
import epyqlib.ticoff
import epyqlib.twisted.busproxy
import functools
import itertools
import platform
import qt5reactor
import signal
import twisted.internet.defer
import twisted.protocols.policies

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from twisted.internet.defer import setDebugging
setDebugging(True)


logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', '-v', action='store_true')
    parser.add_argument('--file', '-f', type=argparse.FileType('rb'))

    return parser.parse_args(args)


def main(args=None):
    app = QApplication(sys.argv)

    if args is None:
        args = sys.argv[1:]

    args = parse_args(args=args)

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logging.getLogger().setLevel(logging.DEBUG)

    qt5reactor.install()
    from twisted.internet import reactor

    default = {
        'Linux': {'bustype': 'socketcan', 'channel': 'can0'},
        'Windows': {'bustype': 'pcan', 'channel': 'PCAN_USBBUS1', 'bitrate': 250000}
    }[platform.system()]

    real_bus = can.interface.Bus(**default)
    bus = epyqlib.busproxy.BusProxy(bus=real_bus)
    protocol = Handler()
    transport = epyqlib.twisted.busproxy.BusProxy(
        protocol=protocol,
        reactor=reactor,
        bus=bus)

    coff = epyqlib.ticoff.Coff()
    coff.from_stream(args.file)

    d = protocol.connect()
    # unlock
    d.addCallbacks(
        lambda _: protocol.set_mta(
            address_extension=AddressExtension.configuration_registers,
            address=0),
        logit
    )
    d.addCallbacks(
        lambda _: protocol.unlock(section=Password.dsp_flash),
        logit
    )
    d.addCallbacks(
        lambda _: protocol.set_mta(
            address_extension=AddressExtension.flash_memory,
            address=0),
        logit
    )
    d.addCallbacks(
        lambda _: protocol.clear_memory()
    )
    # d.addErrback(lambda _: protocol.connect())
    # d.addCallbacks(
    #     lambda _: protocol.set_mta(
    #         address_extension=AddressExtension.flash_memory,
    #         address=0x310000,
    #     ),
    #     logit
    # )

    protocol.continuous_crc = None

    sections = [s for s in coff.sections
                if s.data is not None and s.virt_size > 0]

    for section in sections:
        if len(section.data) % 2 != 0:
            data = itertools.chain(section.data, [0])
        else:
            data = section.data
        callback = functools.partial(
            protocol.download_block,
            address_extension=AddressExtension.flash_memory,
            address=section.virt_addr,
            data=data
        )
        print('0x{:08X}'.format(section.virt_addr))
        d.addCallbacks(lambda _, cb=callback: cb(), logit)

    d.addCallback(lambda _: protocol.build_checksum(
        checksum=protocol.continuous_crc, length=0))
    d.addCallbacks(lambda _: protocol.disconnect(), logit)
    d.addBoth(logit)

    logger.debug('---------- started')
    run_time = 10*60
    QTimer.singleShot(1000 * run_time, reactor.stop)
    QTimer.singleShot(1000 * (run_time + 0.5), app.quit)
    reactor.runReturn()

    return app.exec_()


def logit(it):
    logger.debug('logit(): ({}) {}'.format(type(it), it))

    if isinstance(it, twisted.python.failure.Failure):
        it.printDetailedTraceback()


def endianness_swap_2byte(b):
    # TODO: figure out the correct way to handle endianness, especially
    #       in regard to odd-length data
    # if len(b) % 2 == 1:
    #     b = b.chain(b, [0])

    return itertools.chain(
        *(
            (b, a) for a, b in zip(itertools.islice(b, 0, None, 2),
                                   itertools.islice(b, 1, None, 2))
        )
    )


# http://stackoverflow.com/a/8998040/228539
def chunkit(it, n):
    it = iter(it)
    while True:
        chunk_it = itertools.islice(it, n)
        try:
            first_el = next(chunk_it)
        except StopIteration:
            return
        yield itertools.chain((first_el,), chunk_it)


class HandlerBusy(RuntimeError):
    pass

class HandlerUnknownState(RuntimeError):
    pass

class UnexpectedMessageReceived(ValueError):
    pass

class InvalidSection(ValueError):
    pass


bootloader_can_id = 0x0B081880


@enum.unique
class HandlerState(enum.Enum):
    idle = 0
    connecting = 1
    connected = 2
    disconnecting = 3
    setting_mta = 4
    downloading = 5
    download_6ing = 6
    unlocking = 7
    building_checksum = 8
    clearing_memory = 9


class Handler(twisted.protocols.policies.TimeoutMixin):
    def __init__(self, id=bootloader_can_id, extended=True, parent=None):
        self._deferred = None
        self._stream_deferred = None
        self._internal_deferred = None
        self._chunkit = None
        self._active = False
        self._transport = None

        self._id = id
        self._extended = extended

        self._send_counter = 0

        self._state = HandlerState.idle

        self._remaining_retries = 0

        self._crc = None
        self._crc_length = 0
        self.continuous_crc = None

        self._download_block_counter = 0

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, new_state):
        logger.debug('Entering {}'.format(new_state))
        self._state = new_state

    def makeConnection(self, transport):
        self._transport = transport
        logger.debug('Handler.makeConnection(): {}'.format(transport))

    def connect(self):
        logger.debug('Entering connect()')
        if self._active:
            raise Exception('self._active is True')

        self._deferred = twisted.internet.defer.Deferred()

        if self.state is not HandlerState.idle:
            self.errback(HandlerBusy(
                'Connect requested while {}'.format(self.state.name)))
            return self._deferred

        packet = HostCommand(code=CommandCode.connect)
        # TODO: shouldn't be needed, just makes it agree with oz
        #       for cleaner diff
        packet.payload[0] = 1
        self._send(packet, state=HandlerState.connecting)

        return self._deferred

    def disconnect(self):
        logger.debug('Entering disconnect()')
        if self._active:
            raise Exception('self._active is True')

        self._deferred = twisted.internet.defer.Deferred()

        if self.state is not HandlerState.connected:
            self.errback(HandlerBusy(
                'Disconnect requested while {}'.format(self.state.name)))
            return self._deferred

        packet = HostCommand(code=CommandCode.disconnect)
        self._send(packet, state=HandlerState.disconnecting)

        logger.debug('disconnecting')
        return self._deferred

    def set_mta(self, address_extension, address):
        logger.debug('Entering set_mta()')
        if self._active:
            raise Exception('self._active is True')

        self._deferred = twisted.internet.defer.Deferred()

        if not isinstance(address_extension, AddressExtension):
            self.errback(TypeError(
                'Expected AddressExtension, got: {}'
                    .format(type(address_extension))
            ))
            return self._deferred

        if self.state is not HandlerState.connected:
            self.errback(HandlerBusy(
                'Set MTA requested while {}'.format(self.state.name)))
            return self._deferred

        if (address_extension not in
                [AddressExtension.flash_memory,
                 AddressExtension.configuration_registers]):
            raise Exception('Hardcoded address extension protection')

        if address != 0 and (not 0x310000 <= address <= 0x33fff6+1):
            raise Exception('Hardcoded memory range protection')

        packet = HostCommand(code=CommandCode.set_mta)
        # always zero for Oz bootloader
        packet.payload[0] = 0
        packet.payload[1] = address_extension
        packet.payload[2:6] = address.to_bytes(4, 'big')

        self._send(packet=packet, state=HandlerState.setting_mta)

        return self._deferred

    def unlock(self, section):
        logger.debug('Entering unlock()')
        if self._active:
            raise Exception('self._active is True')

        self._deferred = twisted.internet.defer.Deferred()

        if not isinstance(section, Password):
            self.errback(InvalidSection(
                'Invalid section password specified: {} - {}'.format(
                    section.name, section.value)))
            return self._deferred

        packet = HostCommand(code=CommandCode.unlock)
        packet.payload[0] = 2
        packet.payload[1:3] = section.value.to_bytes(2, 'big')

        self._send(packet=packet, state=HandlerState.unlocking)

        return self._deferred

    def download(self, data):
        logger.debug('Entering download()')

        if self._active:
            raise Exception('self._active is True')

        self._deferred = twisted.internet.defer.Deferred()

        # TODO: figure out the correct way to handle endianness, especially
        #       in regard to odd-length data
        length = len(data)
        if length > 5 or length % 2 != 0:
            self.errback(TypeError(
                'Invalid data length {}'
                    .format(length)
            ))
            return self._deferred

        if self.state is not HandlerState.connected:
            self.errback(HandlerBusy(
                'Download requested while {}'.format(self.state.name)))
            return self._deferred

        packet = HostCommand(code=CommandCode.download)
        swapped_data = tuple(endianness_swap_2byte(data))
        packet.payload[0] = len(swapped_data)
        packet.payload[1:len(swapped_data)+1] = swapped_data

        self._send(packet=packet, state=HandlerState.downloading)

        return self._deferred

    def download_6(self, data):
        logger.debug('Entering download_6()')

        if self._active:
            raise Exception('self._active is True')

        self._deferred = twisted.internet.defer.Deferred()

        if len(data) != 6:
            self.errback(TypeError(
                'Invalid data length {}'
                    .format(len(bytes))
            ))
            return self._deferred

        if self.state is not HandlerState.connected:
            self.errback(HandlerBusy(
                'Download requested while {}'.format(self.state.name)))
            return self._deferred

        packet = HostCommand(code=CommandCode.download_6)
        packet.payload[:] = endianness_swap_2byte(data)

        self._send(packet=packet, state=HandlerState.download_6ing)

        return self._deferred

    def build_checksum(self, checksum, length):
        # length is in bytes, not addresses

        logger.debug('Entering build_checksum()')

        if self._active:
            raise Exception('self._active is True')

        self._deferred = twisted.internet.defer.Deferred()

        if self.state is not HandlerState.connected:
            self.errback(HandlerBusy(
                'Build checksum requested while {}'.format(self.state.name)))
            return self._deferred

        packet = HostCommand(code=CommandCode.build_checksum)
        print(type(length), type(checksum))
        print((length.to_bytes(4, 'big'), checksum.to_bytes(2, 'big')))
        packet.payload[:4] = length.to_bytes(4, 'big')
        packet.payload[4:] = checksum.to_bytes(2, 'big')
        print(packet)

        self._send(packet=packet, state=HandlerState.building_checksum)

        return self._deferred

    def clear_memory(self):
        logger.debug('Entering clear_memory()')
        length = 0xFF

        if self._active:
            raise Exception('self._active is True')

        self._deferred = twisted.internet.defer.Deferred()

        if self.state is not HandlerState.connected:
            self.errback(HandlerBusy(
                'Clear memory requested while {}'.format(self.state.name)))
            return self._deferred

        packet = HostCommand(code=CommandCode.clear_memory)
        packet.payload[:4] = length.to_bytes(4, 'big')

        self._send(packet=packet, state=HandlerState.clearing_memory)

        return self._deferred

    def download_block(self, address_extension, address, data):
        logger.debug('Entering download_block()')
        # print('download_block(address_extension={}, address=0x{:08X})'.format(address_extension, address))
        if self._active:
            raise Exception('self._active is True')

        # self._stream_deferred = twisted.internet.defer.Deferred()

        self._chunkit = chunkit(it=data, n=6)

        # TODO: OOP this
        self._crc = None
        self._crc_length = 0
        self._download_block_counter = 0

        self._internal_deferred = self.set_mta(address_extension, address)
        self._internal_deferred.addCallback(
            lambda _, address=address, address_extension=address_extension:
            self._download_chunk(address=address,
                                 address_extension=address_extension))
        # self._internal_deferred.addCallback(self._stream_deferred.callback)
        self._internal_deferred.addErrback(logit)

        # return self._stream_deferred
        return self._internal_deferred

    def _download_chunk(self, address, address_extension):
        logger.debug('Entering _download_chunk()')
        try:
            chunk = next(self._chunkit)
        except StopIteration:
            chunk = ()
        else:
            chunk = tuple(chunk)

        length = len(chunk)

        download = None
        if length == 6:
            download = self.download_6
        elif length > 0:
            download = self.download

        deferred = None

        if download is not None:
            # TODO: OOP this
            self._crc = crc(data=endianness_swap_2byte(chunk), crc=self._crc)
            self.continuous_crc = crc(data=endianness_swap_2byte(chunk),
                                      crc=self.continuous_crc)
            logger.debug('Continuous CRC: {:04X}'.format(self.continuous_crc))
            self._crc_length += len(chunk)

            self._download_block_counter += 1
            if self._download_block_counter >= 5:
                self._download_block_counter = 0

            deferred = download(data=chunk)
            address += length / 2
            if int(address) != address:
                # TODO: do this better or at least a unique exception
                raise Exception('ack')
            address = int(address)
            if self._download_block_counter == 0:
                deferred.addCallback(
                    lambda _, crc=self._crc, length=self._crc_length:
                    self.build_checksum(checksum=crc, length=length))
                self._crc = None
                self._crc_length = 0
                deferred.addCallback(
                    lambda _, address=address,
                           address_extension=address_extension:
                    self.set_mta(address=address,
                                 address_extension=address_extension)
                )
            # TODO: this just doesn't feel like good structure
            if download is not self.download:
                deferred.addCallback(
                    lambda _, address=address,
                           address_extension=address_extension:
                    self._download_chunk(address=address,
                                 address_extension=address_extension)
                )
        else:
            print('crc: {} {}'.format(type(self._crc), self._crc))
            # l = lambda _: self._internal_deferred.callback(
            #         'Done downloading stream')
            if self._crc is not None:
                deferred = self.build_checksum(checksum=self._crc,
                                               length=self._crc_length)
            #     deferred.addCallback(l)
            # else:
            #     l()

        if deferred is not None:
            deferred.addErrback(logit)#self._stream_deferred.errback)
        else:
            deferred = twisted.internet.defer.Deferred()
            deferred.callback(None)

        return deferred

            # # TODO: this smells...  really bad
        # deferred.callback('Oh so smelly...')
        # return self._internal_deferred

    def _send(self, packet, state):
        packet.command_counter = self._send_counter

        if self._send_counter < 255:
            self._send_counter += 1
        else:
            self._send_counter = 0

        self._transport.write(packet)

        self.state = state

        self.setTimeout(packet.command_code.timeout)

    def dataReceived(self, msg):
        if not (msg.arbitration_id == self._id and
                    bool(msg.id_type) == self._extended):
            return

        if self._active:
            raise Exception('self._active is False')

        self.setTimeout(None)

        packet = Packet.from_message(message=msg)

        if not isinstance(packet, BootloaderReply):
            self.errback(UnexpectedMessageReceived(
                'Not a bootloader reply: {}'.format(packet)))
            return

        logger.debug('packet received: {}'.format(packet.command_return_code.name))

        if self.state is HandlerState.connecting:
            if packet.command_return_code is not CommandStatus.acknowledge:
                self.errback(UnexpectedMessageReceived(
                    'Bootloader should ack when trying to connect, instead: {}'
                        .format(packet)))
                return

            logger.debug('Bootloader version: {major}.{minor}'.format(
                major=packet.payload[0],
                minor=packet.payload[1]
            ))

            dsp_code = (int(packet.payload[2]) << 8) + int(packet.payload[3])

            logger.debug('DSP Part ID: {}'.format(DspCode(dsp_code).name))

            self.state = HandlerState.connected
            self.callback('successfully connected')
        elif self.state is HandlerState.disconnecting:
            if packet.command_return_code is not CommandStatus.acknowledge:
                self.errback(UnexpectedMessageReceived(
                    'Bootloader should ack when trying to disconnect, instead: {}'
                        .format(packet)))
                return

            self.state = HandlerState.idle
            self.callback('successfully disconnected')
        elif self.state is HandlerState.setting_mta:
            if packet.command_return_code is not CommandStatus.acknowledge:
                self.errback(UnexpectedMessageReceived(
                    'Bootloader should ack when trying to set MTA, instead: {}'
                        .format(packet)))
                return

            self.state = HandlerState.connected
            self.callback('successfully set MTA')
        elif self.state is HandlerState.downloading:
            if packet.command_return_code is not CommandStatus.acknowledge:
                self.errback(UnexpectedMessageReceived(
                    'Bootloader should ack when trying to download, instead: {} - {}'
                        .format(packet.command_return_code.name, packet)))
                return

            self.state = HandlerState.connected
            self.callback('successfully downloaded')
        elif self.state is HandlerState.download_6ing:
            if packet.command_return_code is not CommandStatus.acknowledge:
                self.errback(UnexpectedMessageReceived(
                    'Bootloader should ack when trying to download_6, instead: {}'
                        .format(packet)))
                return

            self.state = HandlerState.connected
            self.callback('successfully download_6ed')
        elif self.state is HandlerState.unlocking:
            if packet.command_return_code is not CommandStatus.acknowledge:
                self.errback(UnexpectedMessageReceived(
                    'Bootloader should ack when trying to unlock, instead: {}'
                        .format(packet)))
                return

            self.state = HandlerState.connected
            self.callback('successfully unlocked {}'.format(packet.payload[1]))
        elif self.state is HandlerState.building_checksum:
            if packet.command_return_code is not CommandStatus.acknowledge:
                self.errback(UnexpectedMessageReceived(
                    'Bootloader should ack when trying to build checksum, instead: {}'
                        .format(packet)))
                return

            # TODO: consider verifying returned CRC data beyond just
            #       accepting the embedded side's decision to ack

            self.state = HandlerState.connected
            self.callback('successfully unlocked {}'.format(packet.payload[1]))
        elif self.state is HandlerState.clearing_memory:
            if packet.command_return_code is not CommandStatus.acknowledge:
                self.errback(UnexpectedMessageReceived(
                    'Bootloader should ack when trying to clear memory, instead: {} {}'
                        .format(packet.command_return_code.name, packet)))
                return

            self.state = HandlerState.connected
            self.callback('successfully unlocked {}'.format(packet.payload[1]))
        else:
            self.errback(HandlerUnknownState(
                'Handler in unknown state: {}'.format(self.state)))
            return

    def timeoutConnection(self):
        self._deferred.errback(Exception(
            'Handler timed out while in state: {}'.format(self.state)))

    def callback(self, payload):
        self._active = False
        logger.debug('calling back for {}'.format(self._deferred))
        self._deferred.callback(payload)

    def errback(self, payload):
        self._active = False
        logger.debug('erring back for {}'.format(self._deferred))
        self._deferred.errback(payload)


def crc(data, crc=None):
    if crc is None:
        crc = 0xFFFF

    for byte in data:
        crc ^= byte & 0x00FF

        for _ in range(8):
            if (crc & 0x0001) != 0:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc = (crc >> 1)

    return crc


class IdentifierTypeError(ValueError):
    pass


class PayloadLengthError(ValueError):
    pass


class MessageLengthError(ValueError):
    pass


class WindowSlice:
    def __init__(self, original, offset):
        self._original = original
        self._offset = offset

    def _offsetit(self, key):
        if isinstance(key, slice):
            start = 0 if key.start is None else key.start
            start += self._offset
            stop = None if key.stop is None else key.stop + self._offset

            key = slice(start, stop, key.step)
        else:
            key += self._offset

        return key

    def __getitem__(self, key):
        key = self._offsetit(key)
        return self._original.__getitem__(key)

    def __setitem__(self, key, value):
        key = self._offsetit(key)
        return self._original.__setitem__(key, value)

    def __str__(self):
        return str(self._original[self._offsetit(slice(None))])


class Packet(can.Message):
    def __init__(self, counter_index, payload_start, extended_id=True,
                 arbitration_id=bootloader_can_id, dlc=8, *args, **kwargs):
        # TODO: avoid repetition of required values
        # TODO: block changing of required values
        if not extended_id:
            raise IdentifierTypeError('Identifier type must be set to extended')

        if dlc != 8:
            raise MessageLengthError(
                'Message length must be 8 but is {}'.format(dlc))

        kwargs.setdefault('data', [0] * dlc)

        super().__init__(extended_id=extended_id,
                         arbitration_id=arbitration_id, dlc=dlc,
                         *args, **kwargs)

        self._counter_index = counter_index
        # self._payload_start = payload_start
        self.payload = WindowSlice(self.data, payload_start)

    @classmethod
    def from_message(cls, message):
        if message.data[0] == 0xFF:
            c = BootloaderReply
        else:
            c = HostCommand

        return c(
            code=None,
            timestamp=message.timestamp,
            is_remote_frame=message.is_remote_frame,
            extended_id=message.id_type,
            is_error_frame=message.is_error_frame,
            arbitration_id=message.arbitration_id,
            dlc=message.dlc,
            data=message.data
        )

    # @property
    # def payload(self):
    #     return self.data[self._payload_start:]
    #
    #
    # @payload.setter
    # def payload(self, payload):
    #     maximum_payload = self.dlc - self._payload_start
    #     if len(payload) > maximum_payload:
    #         raise PayloadLengthError(
    #             'Maximum payload length exceeded: {actual} > {maximum}'.format(
    #                 actual=len(payload),
    #                 maximum=maximum_payload
    #             ))
    #
    #     payload += [0] * ((self.dlc - self._payload_start) - len(payload))
    #     self.data[self._payload_start:] = payload

    @property
    def command_counter(self):
        return self.data[self._counter_index]


    @command_counter.setter
    def command_counter(self, counter):
        self.data[self._counter_index] = int(counter)


class HostCommand(Packet):
    def __init__(self, code, *args, **kwargs):
        super().__init__(counter_index=1, payload_start=2, *args, **kwargs)

        if code is not None:
            self.command_code = code

    @property
    def command_code(self):
        return CommandCode(self.data[0])

    @command_code.setter
    def command_code(self, code):
        self.data[0] = int(code)


class BootloaderReply(Packet):
    def __init__(self, code, *args, **kwargs):
        super().__init__(counter_index=2, payload_start=3, *args, **kwargs)

        if code is not None:
            self.command_return_code = code

    @property
    def command_return_code(self):
        return CommandStatus(self.data[1])

    @command_return_code.setter
    def command_return_code(self, code):
        self.data[1] = int(code)


@enum.unique
class CommandCode(enum.IntEnum):
    connect = 0x01
    set_mta = 0x02
    download = 0x03
    disconnect = 0x07
    build_checksum = 0x0E
    clear_memory = 0x10
    unlock = 0x13
    action_service = 0x21
    download_6 = 0x23

    # def __getattr__(self, name):
    #     return getattr(_command_code_properties[self], name)

    @property
    def timeout(self):
        return _command_code_properties[self].timeout


CommandCodeProperties = collections.namedtuple(
    'CommandCodeProperties',
    [
        'timeout'
    ]
)


_command_code_properties = {
    CommandCode.connect: CommandCodeProperties(timeout=1000),
    CommandCode.set_mta: CommandCodeProperties(timeout=1000),
    CommandCode.download: CommandCodeProperties(timeout=1000),
    CommandCode.disconnect: CommandCodeProperties(timeout=1000),
    CommandCode.build_checksum: CommandCodeProperties(timeout=1000),
    CommandCode.clear_memory: CommandCodeProperties(timeout=30000),
    CommandCode.unlock: CommandCodeProperties(timeout=1000),
    CommandCode.action_service: CommandCodeProperties(timeout=1000),
    CommandCode.download_6: CommandCodeProperties(timeout=1000)
}


@enum.unique
class Password(enum.IntEnum):
    dsp_flash = 0x1234
    # eeprom_bootloader = 0xABCD
    # eeprom_data = 0x9876


@enum.unique
class Password(enum.IntEnum):
    dsp_flash = 0x1234
    # eeprom_bootloader = 0xABCD
    # eeprom_data = 0x9876


@enum.unique
class DspCode(enum.IntEnum):
    _2801 = 0x002C
    _2802 = 0x0024
    _2806 = 0x0034
    _2808 = 0x003C
    _2809 = 0x00FE
    _28232 = 0x00E6
    _28234 = 0x00E7
    _28235 = 0x00E8
    _28332 = 0x00ED
    _28334 = 0x00EE
    _28335 = 0x00EF
    _28069PZP = 0x009E
    _28069UPZ = 0x009F
    _28069PFP = 0x009C
    _28069UPFP = 0x009D


@enum.unique
class AddressExtension(enum.IntEnum):
    flash_memory = 0x00
    configuration_registers = 0x01
    eeprom_memory_bootloader = 0x02
    eeprom_memory_data = 0x03


@enum.unique
class ErrorCategory(enum.Enum):
    timeout = -1
    c0 = 0
    c1 = 1
    c2 = 2
    c3 = 3

    @property
    def description(self):
        return _error_category_properties[self].description

    @property
    def action(self):
        return _error_category_properties[self].action

    @property
    def retries(self):
        return _error_category_properties[self].retries


ErrorCategoryProperties = collections.namedtuple(
    'ErrorCategoryProperties',
    [
        'description',
        'action',
        'retries'
    ]
)


_error_category_properties = {
    ErrorCategory.timeout: ErrorCategoryProperties(
        description='No handshake message',
        action='retry',
        retries=2
    ),
    ErrorCategory.c0: ErrorCategoryProperties(
        description='Warning',
        action=None,
        retries=None
    ),
    ErrorCategory.c1: ErrorCategoryProperties(
        description='Spurious (comm. Error, busy, ..)',
        action='Wait (ACK or timeout)',
        retries=2
    ),
    ErrorCategory.c2: ErrorCategoryProperties(
        description='Resolvable (temp, power loss, ..)',
        action='reinitialize',
        retries=1
    ),
    ErrorCategory.c3: ErrorCategoryProperties(
        description='Unresolvable (setup, overload, ..)',
        action='terminate',
        retries=None
    )
}


@enum.unique
class CommandStatus(enum.IntEnum):
    acknowledge = 0x00 # no error
    processor_busy = 0x10
    unknown_command = 0x30
    command_syntax = 0x31
    parameters_out_of_range = 0x32
    access_denied = 0x33
    access_locked = 0x35
    resource_function_unavailable = 0x36
    operational_failure = 0x7F

    @property
    def error_category(self):
        return _command_status_properties[self].error_category

    @property
    def state_transition_to(self):
        return _command_status_properties[self].state_transition_to


CommandStatusProperties = collections.namedtuple(
    'CommandStatusProperties',
    [
        'error_category',
        'state_transition_to'
    ]
)


_command_status_properties = {
    CommandStatus.acknowledge: CommandStatusProperties(
        error_category=None,
        state_transition_to=None
    ),
    CommandStatus.processor_busy: CommandStatusProperties(
        error_category=ErrorCategory.c1,
        state_transition_to='Fault'
    ),
    CommandStatus.unknown_command: CommandStatusProperties(
        error_category=ErrorCategory.c3,
        state_transition_to='Fault'
    ),
    CommandStatus.command_syntax: CommandStatusProperties(
        error_category=ErrorCategory.c3,
        state_transition_to='Fault'
    ),
    CommandStatus.parameters_out_of_range: CommandStatusProperties(
        error_category=ErrorCategory.c3,
        state_transition_to='Fault'
    ),
    CommandStatus.access_denied: CommandStatusProperties(
        error_category=ErrorCategory.c3,
        state_transition_to='Fault'
    ),
    CommandStatus.access_locked: CommandStatusProperties(
        error_category=ErrorCategory.c3,
        state_transition_to='Fault'
    ),
    CommandStatus.resource_function_unavailable: CommandStatusProperties(
        error_category=ErrorCategory.c3,
        state_transition_to='Fault'
    ),
    CommandStatus.operational_failure: CommandStatusProperties(
        error_category=ErrorCategory.c3,
        state_transition_to='Fault'
    ),
}

if __name__ == '__main__':
    import sys
    import traceback

    def excepthook(excType, excValue, tracebackobj):
        logger.debug('Uncaught exception hooked:')
        traceback.print_exception(excType, excValue, tracebackobj)

    sys.excepthook = excepthook
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    sys.exit(main())
