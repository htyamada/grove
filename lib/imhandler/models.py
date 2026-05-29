from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ImageEntry:
    path: Path
    rel_path: Path
    mtime: float


@dataclass
class Album:
    path: Path
    rel_path: Path
    name: str
    depth: int
    children: list['Album'] = field(default_factory=list)
    images: list[ImageEntry] = field(default_factory=list)

    def image_count(self) -> int:
        return len(self.images) + sum(c.image_count() for c in self.children)

    def find(self, rel_path: Path | str) -> 'Album | None':
        """Return the album whose rel_path matches, or None."""
        if str(self.rel_path) == str(rel_path):
            return self
        for child in self.children:
            found = child.find(rel_path)
            if found:
                return found
        return None

    def first_leaf(self) -> 'Album | None':
        """Return the first leaf album in depth-first order, or None."""
        if self.images:
            return self
        for child in self.children:
            leaf = child.first_leaf()
            if leaf:
                return leaf
        return None

    def all_images(self) -> list['ImageEntry']:
        """Return every ImageEntry in the subtree, depth-first."""
        result: list[ImageEntry] = []
        result.extend(self.images)
        for child in self.children:
            result.extend(child.all_images())
        return result
