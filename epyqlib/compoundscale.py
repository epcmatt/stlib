#!/usr/bin/env python3

#TODO: """DocString if there is one"""

import io
import os

from PyQt5 import uic
from PyQt5.QtCore import pyqtProperty, QFile, QFileInfo, QTextStream
from PyQt5.QtWidgets import QWidget

# See file COPYING in this source tree
__copyright__ = 'Copyright 2016, EPC Power Corp.'
__license__ = 'GPLv2+'


class CompoundScale(QWidget):
    def __init__(self, parent=None, in_designer=False):
        QWidget.__init__(self, parent=parent)

        self.in_designer = in_designer

        ui = os.path.join(QFileInfo.absolutePath(QFileInfo(__file__)),
                          'compoundscale.ui')

        # TODO: CAMPid 9549757292917394095482739548437597676742
        if not QFileInfo(ui).isAbsolute():
            ui_file = os.path.join(
                QFileInfo.absolutePath(QFileInfo(__file__)), ui)
        else:
            ui_file = ui
        ui_file = QFile(ui_file)
        ui_file.open(QFile.ReadOnly | QFile.Text)
        ts = QTextStream(ui_file)
        sio = io.StringIO(ts.readAll())
        self.ui = uic.loadUi(sio, self)

        self.ui.command.in_designer = in_designer
        self.ui.echo.in_designer = in_designer
        self.ui.status.in_designer = in_designer
        self.ui.numeric_status.in_designer = in_designer

    @pyqtProperty(str, designable=False)
    def command_frame(self):
        return ''

    @command_frame.setter
    def command_frame(self, value):
        self.command_signal_path_element_0 = value

    @pyqtProperty(str, designable=False)
    def command_signal(self):
        return ''

    @command_signal.setter
    def command_signal(self, value):
        self.command_signal_path_element_1 = value

    @pyqtProperty(str, designable=False)
    def echo_frame(self):
        return ''

    @echo_frame.setter
    def echo_frame(self, value):
        self.echo_signal_path_element_0 = value

    @pyqtProperty(str, designable=False)
    def echo_signal(self):
        return ''

    @echo_signal.setter
    def echo_signal(self, value):
        self.echo_signal_path_element_1 = value

    @pyqtProperty(str, designable=False)
    def status_frame(self):
        return ''

    @status_frame.setter
    def status_frame(self, value):
        self.status_signal_path_element_0 = value

    @pyqtProperty(str, designable=False)
    def status_signal(self):
        return ''

    @status_signal.setter
    def status_signal(self, value):
        self.status_signal_path_element_1 = value

    @pyqtProperty('QString')
    def command_signal_path_element_0(self):
        return self.ui.command.signal_path_element_0

    @command_signal_path_element_0.setter
    def command_signal_path_element_0(self, value):
        self.ui.command.signal_path_element_0 = value

    @pyqtProperty('QString')
    def command_signal_path_element_1(self):
        return self.ui.command.signal_path_element_1

    @command_signal_path_element_1.setter
    def command_signal_path_element_1(self, value):
        self.ui.command.signal_path_element_1 = value

    @pyqtProperty('QString')
    def command_signal_path_element_2(self):
        return self.ui.command.signal_path_element_2

    @command_signal_path_element_2.setter
    def command_signal_path_element_2(self, value):
        self.ui.command.signal_path_element_2 = value


    @pyqtProperty('QString')
    def echo_signal_path_element_0(self):
        return self.ui.echo.signal_path_element_0

    @echo_signal_path_element_0.setter
    def echo_signal_path_element_0(self, value):
        self.ui.echo.signal_path_element_0 = value

    @pyqtProperty('QString')
    def echo_signal_path_element_1(self):
        return self.ui.echo.signal_path_element_1

    @echo_signal_path_element_1.setter
    def echo_signal_path_element_1(self, value):
        self.ui.echo.signal_path_element_1 = value

    @pyqtProperty('QString')
    def echo_signal_path_element_2(self):
        return self.ui.echo.signal_path_element_2

    @echo_signal_path_element_2.setter
    def echo_signal_path_element_2(self, value):
        self.ui.echo.signal_path_element_2 = value


    @pyqtProperty('QString')
    def status_signal_path_element_0(self):
        return self.ui.status.signal_path_element_0

    @status_signal_path_element_0.setter
    def status_signal_path_element_0(self, value):
        self.ui.status.signal_path_element_0 = value
        self.ui.numeric_status.signal_path_element_0 = value

    @pyqtProperty('QString')
    def status_signal_path_element_1(self):
        return self.ui.status.signal_path_element_1

    @status_signal_path_element_1.setter
    def status_signal_path_element_1(self, value):
        self.ui.status.signal_path_element_1 = value
        self.ui.numeric_status.signal_path_element_1 = value

    @pyqtProperty('QString')
    def status_signal_path_element_2(self):
        return self.ui.status.signal_path_element_2

    @status_signal_path_element_2.setter
    def status_signal_path_element_2(self, value):
        self.ui.status.signal_path_element_2 = value
        self.ui.numeric_status.signal_path_element_2 = value

    @pyqtProperty(bool)
    def status_override_range(self):
        return self.ui.status.override_range

    @status_override_range.setter
    def status_override_range(self, override):
        self.ui.status.override_range = override

    @pyqtProperty(float)
    def status_minimum(self):
        return self.ui.status.minimum

    @status_minimum.setter
    def status_minimum(self, min):
        self.ui.status.minimum = float(min)

    @pyqtProperty(float)
    def status_maximum(self):
        return self.ui.status.maximum

    @status_maximum.setter
    def status_maximum(self, max):
        self.ui.status.maximum = float(max)

    @pyqtProperty(str)
    def status_label(self):
        return self.ui.numeric_status.label_override

    @status_label.setter
    def status_label(self, label):
        self.ui.numeric_status.label_override = label


if __name__ == '__main__':
    import sys

    print('No script functionality here')
    sys.exit(1)     # non-zero is a failure
