Shared files for the mediaview app of grove/llime and qat/knip. Provides a way to browse image and video files.

The canonical copy lives at `~/prj/grove/lib/mediaview`. Host Django projects
load it by adding `~/prj/grove/lib` to `sys.path`, setting `MEDIAVIEW_LABEL`,
and including `mediaview` in `INSTALLED_APPS`.
