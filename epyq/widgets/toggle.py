#!/usr/bin/env python3

#TODO: """DocString if there is one"""

import epyq.widgets.abstracttxwidget
import os
from PyQt5.QtCore import (pyqtSignal, pyqtProperty,
                          QFile, QFileInfo, QTextStream, Qt, QEvent,
                          QTimer)
from PyQt5.QtGui import QMouseEvent

# See file COPYING in this source tree
__copyright__ = 'Copyright 2016, EPC Power Corp.'
__license__ = 'GPLv2+'


class Toggle(epyq.widgets.abstracttxwidget.AbstractTxWidget):
    def __init__(self, parent=None):
        ui_file = os.path.join(QFileInfo.absolutePath(QFileInfo(__file__)),
                               'toggle.ui')

        epyq.widgets.abstracttxwidget.AbstractTxWidget.__init__(self,
                ui=ui_file, parent=parent)

        self.ui.value.installEventFilter(self)
        # TODO: CAMPid 398956661298765098124690765
        self.ui.value.valueChanged.connect(self.widget_value_changed)

        self._frame = None
        self._signal = None

    def eventFilter(self, qobject, qevent):
        if isinstance(qevent, QMouseEvent) and self.tx:
            if (qevent.button() == Qt.LeftButton and
                        qevent.type() == QEvent.MouseButtonRelease):
                self.toggle_released()

            return True

        return False

    def set_value(self, value):
        # TODO: quit hardcoding this and it's better implemented elsewhere
        if self.signal_object is not None:
            value = bool(self.signal_object.value)
        elif value is None:
            value = False
        else:
            value = bool(value)

        self.ui.value.setSliderPosition(value)

    def toggle_released(self):
        if self.ui.value.sliderPosition():
            self.ui.value.setSliderPosition(False)
        else:
            self.ui.value.setSliderPosition(True)

    def set_signal(self, signal):
        if signal is not self.signal_object:
            if signal is not None:
                self.ui.off.setText(signal.signal._values['0'])
                self.ui.on.setText(signal.signal._values['1'])
                signal.value_changed.connect(self.signal_value_changed)
            else:
                self.ui.off.setText('-')
                self.ui.on.setText('-')
        epyq.widgets.abstracttxwidget.AbstractTxWidget.set_signal(self, signal)


if __name__ == '__main__':
    import sys

    print('No script functionality here')
    sys.exit(1)     # non-zero is a failure