Shared, reusable Django app for browsing and lightly previewing a directory
tree of PDF/EPUB/CBZ/Markdown/text documents, and staging a selected format
into a separate active-reader directory. It is a browser and selection
tool, not an online reader.

The canonical copy lives at `~/prj/grove/lib/documentview`. Host Django
projects load it by adding `~/prj/grove/lib` to `sys.path`, setting
`DOCUMENT_VIEWER_ROOT` / `DOCUMENT_VIEWER_ACTIVE_DIR`, and including
`documentview` in `INSTALLED_APPS`. See `AGENTS.md` for the full
configuration reference, security model, and testing guidelines.
