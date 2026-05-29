import json
import mimetypes
import os
import shutil
from pathlib import Path
from urllib.parse import quote

from django.http import Http404, JsonResponse, FileResponse
from django.shortcuts import render
from django.urls import get_script_prefix
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from base.lib.tools import nav as base_nav
from . import conf, thumbs

_nav = []

_TEMPLATE = 'mediaview/browse.html'
_APP = 'mediaview'
_STEM_SIDECAR_SUFFIXES = frozenset({'.aae', '.xmp'})
_RENAME_IMAGE_SUFFIXES = frozenset({'.png', '.jpg', '.gif', '.tiff'})
_RENAME_SUFFIX_ALIASES = {
    '.jpeg': '.jpg',
    '.tif': '.tiff',
}


def _prefix():
    return get_script_prefix().rstrip('/')


def _url(*parts):
    return _prefix() + '/' + '/'.join(str(p) for p in parts)


def _get_roots():
    try:
        return conf.roots()
    except FileNotFoundError:
        return None


def _resolve(root_name, subpath=''):
    roots = conf.roots()
    if root_name not in roots:
        raise Http404('Unknown root')
    root = roots[root_name]
    if subpath:
        target = (root / subpath).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise Http404('Invalid path')
        return root, target
    return root, root


def _associated_sidecars(path: Path) -> list[Path]:
    sidecars = []
    seen = set()

    try:
        candidates = path.parent.iterdir()
    except OSError:
        return sidecars

    for candidate in candidates:
        if candidate == path or not candidate.is_file() or thumbs.is_media(candidate):
            continue

        is_sidecar = (
            candidate.name.startswith(path.name + '.')
            or (
                candidate.stem == path.stem
                and candidate.suffix.lower() in _STEM_SIDECAR_SUFFIXES
            )
        )
        if is_sidecar and candidate not in seen:
            sidecars.append(candidate)
            seen.add(candidate)

    return sorted(sidecars, key=lambda p: p.name.lower())


def _renamed_sidecar_path(sidecar: Path, old_path: Path, new_path: Path) -> Path:
    if sidecar.name.startswith(old_path.name + '.'):
        suffix = sidecar.name[len(old_path.name):]
        return new_path.parent / (new_path.name + suffix)
    return new_path.parent / (new_path.stem + sidecar.suffix)


def _rename_suffix(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in _RENAME_IMAGE_SUFFIXES:
        return suffix
    return _RENAME_SUFFIX_ALIASES.get(suffix)


def _item_for_file(root_name, root, entry):
    rel = entry.relative_to(root)
    rel_str = str(rel)
    enc = quote(rel_str, safe='/')
    rn = quote(root_name)
    try:
        stat = entry.stat()
        thumb_version = f'?v={stat.st_mtime_ns}-{stat.st_size}-{stat.st_ino}'
    except OSError:
        thumb_version = ''

    sidecar_path = entry.parent / (entry.name + '.json')
    sidecar = None
    if sidecar_path.exists():
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding='utf-8'))
        except Exception:
            sidecar = {}

    return {
        'type': 'video' if thumbs.is_video(entry) else 'image',
        'fname': entry.name,
        'relpath': rel_str,
        'thumb_url':       _url(_APP, 'thumb',       rn, enc) + thumb_version,
        'large_thumb_url': _url(_APP, 'large-thumb', rn, enc) + thumb_version,
        'url':             _url(_APP, 'file',        rn, enc),
        'info_url':        _url(_APP, 'info',        rn, enc),
        'has_sidecar': sidecar is not None,
        'sidecar': sidecar,
    }


def _ctx(title, extra):
    return {'title': title, 'base_nav': base_nav, 'nav': _nav, **extra}


def _move_roots(roots):
    return [
        {
            'name': name,
            'dir_list_url': _url(_APP, 'dirs', quote(name)) + '/',
        }
        for name in roots
    ]


@ensure_csrf_cookie
def index(request):
    roots = _get_roots()
    if roots is None:
        return render(request, _TEMPLATE, _ctx('Media Viewer', {
            'error': '~/etc/mediaview.conf not found.',
            'breadcrumbs': [], 'parent_url': None, 'dirs': [], 'items': [],
            'root_name': '', 'delete_url': '', 'metadata_url': '', 'dir_list_url': '',
            'rename_url': '',
            'move_roots': [],
        }))
    dirs = [{'name': name, 'url': _url(_APP, 'browse', quote(name)) + '/'} for name in roots]
    return render(request, _TEMPLATE, _ctx('Media Viewer', {
        'breadcrumbs': [],
        'parent_url': None,
        'parent_relpath': None,
        'dirs': dirs,
        'items': [],
        'root_name': '',
        'delete_url': _url(_APP, 'delete') + '/',
        'rename_url': _url(_APP, 'rename') + '/',
        'metadata_url': _url(_APP, 'metadata') + '/',
        'move_url': _url(_APP, 'move') + '/',
        'dir_list_url': '',
        'move_roots': _move_roots(roots),
    }))


@ensure_csrf_cookie
def browse(request, root_name, subpath=''):
    root, directory = _resolve(root_name, subpath)
    if not directory.is_dir():
        raise Http404('Not a directory')

    rel = directory.relative_to(root)
    rn = quote(root_name)
    breadcrumbs = [{'name': root_name, 'url': _url(_APP, 'browse', rn) + '/'}]
    accumulated = []
    for part in rel.parts:
        accumulated.append(part)
        crumb_path = quote('/'.join(accumulated), safe='/')
        breadcrumbs.append({
            'name': part,
            'url': _url(_APP, 'browse', rn, crumb_path) + '/',
        })

    if directory == root:
        parent_url = _url(_APP) + '/'
    else:
        parent_parts = rel.parts[:-1]
        if parent_parts:
            parent_path = quote('/'.join(parent_parts), safe='/')
            parent_url = _url(_APP, 'browse', rn, parent_path) + '/'
        else:
            parent_url = _url(_APP, 'browse', rn) + '/'

    try:
        entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        raise Http404('Permission denied')

    dirs = []
    items = []

    for entry in entries:
        if entry.name.startswith('.'):
            continue
        if entry.is_dir():
            rel_entry = entry.relative_to(root)
            enc_rel = quote(str(rel_entry), safe='/')
            dirs.append({
                'name': entry.name,
                'url': _url(_APP, 'browse', rn, enc_rel) + '/',
                'relpath': str(rel_entry),
            })
        elif entry.is_file() and thumbs.is_media(entry):
            items.append(_item_for_file(root_name, root, entry))

    parent_relpath = None if directory == root else '/'.join(rel.parts[:-1])
    current_relpath = '' if directory == root else str(rel)

    return render(request, _TEMPLATE, _ctx(f'Media — {root_name}', {
        'root_name': root_name,
        'breadcrumbs': breadcrumbs,
        'parent_url': parent_url,
        'parent_relpath': parent_relpath,
        'current_relpath': current_relpath,
        'dirs': dirs,
        'items': items,
        'delete_url': _url(_APP, 'delete') + '/',
        'rename_url': _url(_APP, 'rename') + '/',
        'metadata_url': _url(_APP, 'metadata') + '/',
        'move_url': _url(_APP, 'move') + '/',
        'mkdir_url': _url(_APP, 'mkdir') + '/',
        'dir_list_url': _url(_APP, 'dirs', rn) + '/',
        'move_roots': _move_roots(conf.roots()),
    }))


def thumbnail(request, root_name, subpath):
    return _serve_thumb(request, root_name, subpath, 200)


def large_thumbnail(request, root_name, subpath):
    return _serve_thumb(request, root_name, subpath, 600)


def _serve_thumb(request, root_name, subpath, long_edge):
    _, target = _resolve(root_name, subpath)
    if not target.is_file():
        raise Http404
    thumb = thumbs.get_or_create(target, long_edge)
    if thumb is None:
        raise Http404('No thumbnail')
    return FileResponse(open(thumb, 'rb'), content_type='image/jpeg')


def serve_file(request, root_name, subpath):
    _, target = _resolve(root_name, subpath)
    if not target.is_file():
        raise Http404
    content_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(open(target, 'rb'), content_type=content_type or 'application/octet-stream')


def info(request, root_name, subpath):
    _, target = _resolve(root_name, subpath)
    if not target.is_file():
        raise Http404

    sidecar_path = target.parent / (target.name + '.json')
    if sidecar_path.exists():
        try:
            data = json.loads(sidecar_path.read_text(encoding='utf-8'))
            return JsonResponse({'source': 'sidecar', 'data': data})
        except Exception:
            pass

    if thumbs.is_image(target):
        return JsonResponse({'source': 'exif', 'data': _get_exif(target)})

    return JsonResponse({'source': 'none', 'data': {}})


def dirs(request, root_name):
    relpath = request.GET.get('path', '')
    try:
        root, directory = _resolve(root_name, relpath)
    except Http404:
        return JsonResponse({'error': 'Invalid path'}, status=400)

    if not directory.is_dir():
        return JsonResponse({'error': 'Not a directory'}, status=400)

    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    rel = directory.relative_to(root)
    if directory == root:
        parent_relpath = None
    elif directory.parent == root:
        parent_relpath = ''
    else:
        parent_relpath = str(directory.parent.relative_to(root))
    subdirs = []
    for entry in entries:
        if entry.name.startswith('.'):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if not is_dir:
            continue
        subdirs.append({
            'name': entry.name,
            'relpath': str(entry.relative_to(root)),
            'writable': os.access(entry, os.W_OK | os.X_OK),
        })

    return JsonResponse({
        'root_name': root_name,
        'relpath': '' if directory == root else str(rel),
        'display_path': root_name if directory == root else f'{root_name}/{rel}',
        'parent_relpath': parent_relpath,
        'writable': os.access(directory, os.W_OK | os.X_OK),
        'dirs': subdirs,
    })


def _get_exif(path: Path) -> dict:
    try:
        from PIL import Image, ExifTags
        img = Image.open(path)
        raw = img._getexif()
        if not raw:
            return {}
        skip = {
            'MakerNote', 'UserComment', 'FlashPixVersion', 'ExifVersion',
            'ComponentsConfiguration', 'SceneType', 'FileSource',
            'PrintImageMatching', 'InteroperabilityIndex', 'InteroperabilityVersion',
        }
        result = {}
        for tag_id, value in raw.items():
            tag = ExifTags.TAGS.get(tag_id, str(tag_id))
            if tag in skip or isinstance(value, bytes):
                continue
            if isinstance(value, tuple) and len(value) == 2:
                try:
                    num, den = value
                    result[tag] = f'{num}/{den}' if den != 0 else str(num)
                except Exception:
                    result[tag] = str(value)
            else:
                result[tag] = str(value)
        return result
    except Exception:
        return {}


@require_POST
def save_metadata(request):
    try:
        data = json.loads(request.body)
        root_name = data['root']
        relpath = data['relpath']
        metadata = data['metadata']
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if not isinstance(metadata, dict):
        return JsonResponse({'error': 'Metadata must be a JSON object'}, status=400)

    try:
        _, target = _resolve(root_name, relpath)
    except Http404:
        return JsonResponse({'error': 'Invalid path'}, status=400)

    if not target.is_file() or not thumbs.is_media(target):
        return JsonResponse({'error': 'Media file not found'}, status=404)

    if not os.access(target.parent, os.W_OK | os.X_OK):
        return JsonResponse({'error': 'Directory is not writable'}, status=400)

    sidecar_path = target.parent / (target.name + '.json')
    try:
        sidecar_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    except OSError as e:
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'ok': True, 'metadata': metadata})


@require_POST
def delete_file(request):
    try:
        data = json.loads(request.body)
        root_name = data['root']
        relpath = data['relpath']
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    try:
        root, target = _resolve(root_name, relpath)
    except Http404:
        return JsonResponse({'error': 'Invalid path'}, status=400)

    if not target.is_file():
        return JsonResponse({'error': 'File not found'}, status=404)

    for sidecar in _associated_sidecars(target):
        try:
            sidecar.unlink()
        except OSError:
            pass

    try:
        target.unlink()
    except OSError as e:
        return JsonResponse({'error': str(e)}, status=500)

    thumbs.invalidate(target)

    return JsonResponse({'ok': True})


@require_POST
def rename_file(request):
    try:
        data = json.loads(request.body)
        root_name = data['root']
        relpath = data['relpath']
        new_name = data['name']
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if not new_name or '/' in new_name or '\\' in new_name or new_name in ('.', '..'):
        return JsonResponse({'error': 'Invalid filename'}, status=400)
    if new_name.startswith('.'):
        return JsonResponse({'error': 'Dotfiles are not allowed'}, status=400)
    if Path(new_name).suffix:
        return JsonResponse({'error': 'Enter the filename without an extension'}, status=400)

    try:
        _, target = _resolve(root_name, relpath)
    except Http404:
        return JsonResponse({'error': 'Invalid path'}, status=400)

    if not target.is_file() or not thumbs.is_media(target):
        return JsonResponse({'error': 'Media file not found'}, status=404)

    suffix = _rename_suffix(target)
    if suffix is None:
        return JsonResponse({'error': 'Only .png, .jpg, .gif, and .tiff files can be renamed'}, status=400)

    new_path = target.parent / f'{new_name}{suffix}'
    if new_path == target:
        return JsonResponse({'error': 'Filename is unchanged'}, status=400)
    if new_path.exists():
        return JsonResponse({'error': f'{new_path.name!r} already exists'}, status=400)
    if not os.access(target.parent, os.W_OK | os.X_OK):
        return JsonResponse({'error': 'Directory is not writable'}, status=400)

    moves = [(target, new_path)]
    for sidecar_src in _associated_sidecars(target):
        sidecar_dest = _renamed_sidecar_path(sidecar_src, target, new_path)
        if sidecar_dest.exists():
            return JsonResponse({'error': f'{sidecar_dest.name!r} already exists'}, status=400)
        moves.append((sidecar_src, sidecar_dest))

    for move_src, move_dest in moves:
        try:
            move_src.rename(move_dest)
        except OSError as e:
            return JsonResponse({'error': str(e)}, status=500)

    thumbs.invalidate(target)
    thumbs.invalidate(new_path)

    return JsonResponse({'ok': True})


@require_POST
def mkdir(request):
    try:
        data = json.loads(request.body)
        root_name = data['root']
        relpath = data.get('relpath', '')
        name = data['name']
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if not name or '/' in name or '\\' in name or name in ('.', '..'):
        return JsonResponse({'error': 'Invalid directory name'}, status=400)

    try:
        root, directory = _resolve(root_name, relpath)
    except Http404:
        return JsonResponse({'error': 'Invalid path'}, status=400)

    if not directory.is_dir():
        return JsonResponse({'error': 'Parent is not a directory'}, status=400)

    new_dir = directory / name
    if new_dir.exists():
        return JsonResponse({'error': f'"{name}" already exists'}, status=400)

    try:
        new_dir.mkdir()
    except OSError as e:
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'ok': True})


@require_POST
def move_file(request):
    try:
        data = json.loads(request.body)
        root_name = data['root']
        relpath = data['relpath']
        dest_dir = data['dest_dir']
        dest_root_name = data.get('dest_root', root_name)
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    try:
        _, src = _resolve(root_name, relpath)
    except Http404:
        return JsonResponse({'error': 'Invalid source path'}, status=400)

    if not src.is_file():
        return JsonResponse({'error': 'Source not found'}, status=404)

    try:
        _, dest_dir_path = _resolve(dest_root_name, dest_dir)
    except Http404:
        return JsonResponse({'error': 'Invalid destination'}, status=400)

    if not dest_dir_path.is_dir():
        return JsonResponse({'error': 'Destination is not a directory'}, status=400)

    if not os.access(dest_dir_path, os.W_OK | os.X_OK):
        return JsonResponse({'error': 'Destination is not writable'}, status=400)

    if dest_dir_path == src.parent:
        return JsonResponse({'error': 'Already in that directory'}, status=400)

    dest_file = dest_dir_path / src.name
    if dest_file.exists():
        return JsonResponse({'error': f'{src.name!r} already exists in destination'}, status=400)

    moves = [(src, dest_file)]
    for sidecar_src in _associated_sidecars(src):
        sidecar_dest = dest_dir_path / sidecar_src.name
        if sidecar_dest.exists():
            return JsonResponse({'error': f'{sidecar_src.name!r} already exists in destination'}, status=400)
        moves.append((sidecar_src, sidecar_dest))

    for move_src, move_dest in moves:
        try:
            shutil.move(str(move_src), str(move_dest))
        except OSError as e:
            return JsonResponse({'error': str(e)}, status=500)

    thumbs.invalidate(src)
    thumbs.invalidate(dest_file)

    return JsonResponse({'ok': True})
