"""
Command Pattern implementation for Undo/Redo subsystem.
Replaces global snapshots with local state capture for high performance.
"""

from abc import ABC, abstractmethod
from typing import List, Any, Optional
import copy
from .subtitle import SubtitleManager, SubtitleSegment


class Command(ABC):
    """Abstract base class for all reversible commands."""

    def __init__(self, manager: "SubtitleManager"):
        self.manager = manager

    @abstractmethod
    def execute(self) -> bool:
        """Execute the command. Returns True if successful."""
        pass

    @abstractmethod
    def undo(self):
        """Revert the command effects."""
        pass

    def redo(self):
        """Redo is typically just execute, but can be overridden."""
        return self.execute()


class SplitSegmentCommand(Command):
    """Command to split a segment at a specific time."""

    def __init__(self, manager: "SubtitleManager", segment_id: str, split_time: float):
        super().__init__(manager)
        self.segment_id = segment_id
        self.split_time = split_time

        # State for Undo
        self.original_segment_state: Optional[SubtitleSegment] = None
        self.new_segment_id: Optional[str] = None

    def execute(self) -> bool:
        # Capture state before mutation if first run
        target = self.manager.get_segment(self.segment_id)
        if not target:
            return False

        # We need a deep copy of the original segment BEFORE split
        if self.original_segment_state is None:
            self.original_segment_state = copy.deepcopy(target)

        # Perform Split (logic in manager)
        # manager.split_segment returns (new_id, old_seg, new_seg)
        new_id, _, _ = self.manager.split_segment(
            self.segment_id, self.split_time, save_undo=False
        )

        if new_id:
            self.new_segment_id = new_id
            return True
        return False

    def undo(self):
        if not self.original_segment_state or not self.new_segment_id:
            return

        # 1. Remove the newly created segment
        self.manager.delete_segments([self.new_segment_id], save_undo=False)

        # 2. Restore the original segment state
        # We find the current version of the original segment (which was shortened)
        # and replace its attributes with the saved state.
        current = self.manager.get_segment(self.segment_id)
        if current:
            self._restore_segment(current, self.original_segment_state)
        else:
            # If it was somehow deleted, add it back (unlikely in simple stack)
            self.manager.add_segment(
                copy.deepcopy(self.original_segment_state), save_undo=False
            )

    def _restore_segment(self, target: SubtitleSegment, source: SubtitleSegment):
        """Helper to restore attributes."""
        target.start = source.start
        target.end = source.end
        target.text = source.text
        target.words = copy.deepcopy(source.words)
        target.status = source.status


class MergeSegmentsCommand(Command):
    """Command to merge multiple segments."""

    def __init__(self, manager: "SubtitleManager", segment_ids: List[str]):
        super().__init__(manager)
        self.segment_ids = segment_ids

        # State
        self.original_segments: List[SubtitleSegment] = []
        self.merged_segment_id: Optional[str] = None

    def execute(self) -> bool:
        # Capture state
        if not self.original_segments:
            for sid in self.segment_ids:
                seg = self.manager.get_segment(sid)
                if seg:
                    self.original_segments.append(copy.deepcopy(seg))

            # Sort by time to ensure consistent restoration
            self.original_segments.sort(key=lambda s: s.start)

        if not self.original_segments:
            return False

        # Perform Merge
        merged_seg, _ = self.manager.merge_segments(self.segment_ids, save_undo=False)

        if merged_seg:
            self.merged_segment_id = merged_seg.id
            return True
        return False

    def undo(self):
        if not self.merged_segment_id:
            return

        # 1. Remove the merged segment
        self.manager.delete_segments([self.merged_segment_id], save_undo=False)

        # 2. Restore original segments
        # We simply add them back. Manager sorts them automatically or we rely on logic.
        for seg in self.original_segments:
            # Check if exists (paranoia)
            if not self.manager.get_segment(seg.id):
                self.manager.add_segment(copy.deepcopy(seg), save_undo=False)


class DeleteSegmentsCommand(Command):
    """Command to delete segments."""

    def __init__(self, manager: "SubtitleManager", segment_ids: List[str]):
        super().__init__(manager)
        self.segment_ids = segment_ids
        self.deleted_segments: List[SubtitleSegment] = []

    def execute(self) -> bool:
        # Capture state
        if not self.deleted_segments:
            for sid in self.segment_ids:
                seg = self.manager.get_segment(sid)
                if seg:
                    self.deleted_segments.append(copy.deepcopy(seg))

        if not self.deleted_segments:
            return False

        self.manager.delete_segments(self.segment_ids, save_undo=False)
        return True

    def undo(self):
        # Restore all deleted segments
        for seg in self.deleted_segments:
            if not self.manager.get_segment(seg.id):
                self.manager.add_segment(copy.deepcopy(seg), save_undo=False)


class UpdateTextCommand(Command):
    """Command to update text of a segment."""

    def __init__(self, manager: "SubtitleManager", segment_id: str, new_text: str):
        super().__init__(manager)
        self.segment_id = segment_id
        self.new_text = new_text
        self.old_text: Optional[str] = None

    def execute(self) -> bool:
        seg = self.manager.get_segment(self.segment_id)
        if not seg:
            return False

        if self.old_text is None:
            self.old_text = seg.text

        # If text hasn't changed, don't do anything (optional optimization, handled by caller?)
        if seg.text == self.new_text:
            return False

        self.manager.update_text(self.segment_id, self.new_text, save_undo=False)
        return True

    def undo(self):
        if self.old_text is not None:
            self.manager.update_text(self.segment_id, self.old_text, save_undo=False)


class GenericSnapshotCommand(Command):
    """
    Fallback command that saves the entire state.
    Used for complex operations like drag-resize with collision resolution.
    Captures state Before and After execution.
    """

    def __init__(self, manager: "SubtitleManager", action_callback):
        super().__init__(manager)
        self.action_callback = action_callback
        self.before: Optional[List[SubtitleSegment]] = None
        self.after: Optional[List[SubtitleSegment]] = None

    def execute(self) -> bool:
        if self.before is None:
            self.before = copy.deepcopy(self.manager.segments)

        self.action_callback()

        if self.after is None:
            self.after = copy.deepcopy(self.manager.segments)
        return True

    def undo(self):
        if self.before is not None:
            # Restore 'before' state
            # We access _segments directly as we are in the engine package
            if hasattr(self.manager, "_segments"):
                self.manager._segments = copy.deepcopy(self.before)

    def redo(self) -> bool:
        if self.after is not None:
            if hasattr(self.manager, "_segments"):
                self.manager._segments = copy.deepcopy(self.after)
            return True
        return False
