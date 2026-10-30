from PyQt5.QtCore import QPoint, QRect, QSize, Qt
from PyQt5.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """自适应流式换行布局管理器 (Qt 经典 FlowLayout 实现)
    按可用宽度动态将控件流式排布到各行，当一行宽度不足时自动换行，
    并垂直居中同一行内的所有控件。
    """

    def __init__(self, parent=None, margin=0, spacing=6):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self.itemList = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self.itemList.append(item)

    def count(self):
        return len(self.itemList)

    def itemAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList.pop(index)
        return None

    def clear(self):
        while self.count():
            item = self.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._doLayout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._doLayout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.itemList:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _doLayout(self, rect, test_only):
        m = self.contentsMargins()
        effective_rect = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective_rect.x()
        y = effective_rect.y()
        spacing = self.spacing()

        lines = []
        cur_line = []
        cur_line_w = 0

        for item in self.itemList:
            item_w = item.sizeHint().width()
            if cur_line and (cur_line_w + spacing + item_w > effective_rect.width()):
                lines.append(cur_line)
                cur_line = [item]
                cur_line_w = item_w
            else:
                if cur_line:
                    cur_line_w += spacing + item_w
                else:
                    cur_line_w = item_w
                cur_line.append(item)

        if cur_line:
            lines.append(cur_line)

        cur_y = y
        for line in lines:
            line_height = max((it.sizeHint().height() for it in line), default=0)
            cur_x = x
            for it in line:
                it_sz = it.sizeHint()
                if not test_only:
                    it_y = cur_y + (line_height - it_sz.height()) // 2
                    it.setGeometry(QRect(QPoint(cur_x, it_y), it_sz))
                cur_x += it_sz.width() + spacing
            cur_y += line_height + spacing

        total_h = cur_y - spacing if lines else y
        return total_h - rect.y() + m.bottom()
