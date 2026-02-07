from PySide6.QtWidgets import QDialog
from PySide6.QtCore import Qt, QPoint


class MagneticDialog(QDialog):
    """
    A QDialog that snaps to the edges of its parent widget.
    """

    SNAP_DISTANCE = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_snapping = False

    def moveEvent(self, event):
        """Handle window move to implement snapping."""
        super().moveEvent(event)

        # Avoid recursion
        if self._is_snapping:
            return

        parent = self.parent()
        if not parent or not parent.isVisible():
            return

        # Only snap if mouse button is pressed (dragging)
        # Note: This checks if *any* interaction is happening,
        # strictly speaking moveEvent comes from OS, checking mouse state helps avoid self-correction loops when not dragging.
        # However, accurate detection of "user dragging title bar" is hard in pure Qt.
        # We will try a simple proximity check.

        curr_pos = self.pos()
        new_pos = QPoint(curr_pos)

        my_geo = self.frameGeometry()
        parent_geo = parent.frameGeometry()

        snapped = False

        # X-axis snapping
        # Left to Parent Left (Inner)
        if abs(my_geo.left() - parent_geo.left()) < self.SNAP_DISTANCE:
            new_pos.setX(parent_geo.left())
            snapped = True
        # Left to Parent Right (Outer)
        elif abs(my_geo.left() - parent_geo.right()) < self.SNAP_DISTANCE:
            new_pos.setX(parent_geo.right())
            snapped = True
        # Right to Parent Left (Outer)
        elif abs(my_geo.right() - parent_geo.left()) < self.SNAP_DISTANCE:
            new_pos.setX(parent_geo.left() - my_geo.width())
            snapped = True
        # Right to Parent Right (Inner)
        elif abs(my_geo.right() - parent_geo.right()) < self.SNAP_DISTANCE:
            new_pos.setX(parent_geo.right() - my_geo.width())
            snapped = True

        # Y-axis snapping
        # Top to Parent Top (Inner)
        if abs(my_geo.top() - parent_geo.top()) < self.SNAP_DISTANCE:
            new_pos.setY(parent_geo.top())
            snapped = True
        # Top to Parent Bottom (Outer)
        elif abs(my_geo.top() - parent_geo.bottom()) < self.SNAP_DISTANCE:
            new_pos.setY(parent_geo.bottom())
            snapped = True
        # Bottom to Parent Top (Outer)
        elif abs(my_geo.bottom() - parent_geo.top()) < self.SNAP_DISTANCE:
            new_pos.setY(parent_geo.top() - my_geo.height())
            snapped = True
        # Bottom to Parent Bottom (Inner)
        elif abs(my_geo.bottom() - parent_geo.bottom()) < self.SNAP_DISTANCE:
            new_pos.setY(parent_geo.bottom() - my_geo.height())
            snapped = True

        if snapped and new_pos != curr_pos:
            self._is_snapping = True
            self.move(new_pos)
            self._is_snapping = False
