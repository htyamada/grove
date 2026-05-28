from django.urls import reverse_lazy
from base.lib.tools import nav as base_nav, specs_nav_item
from hty7.llemon.djview import LLemonViewSet
from hty7.llemon.djview.media import LLemonMediaViewSet, bind_llemon_views

_llemon_nav = [
    {'name': 'LLemon Persona', 'url': reverse_lazy('llemon_persona:index')},
    {'name': 'LLemon Media',   'url': reverse_lazy('llemon:media')},
]
_llemon_nav_suffix = [specs_nav_item('llemon')]
_llemon_specs_url = _llemon_nav_suffix[0]['url']

_vs = LLemonViewSet(
    'llemon_persona', 'llemon_persona',
    base_nav=base_nav, nav=_llemon_nav, nav_suffix=_llemon_nav_suffix,
)
_mvs = LLemonMediaViewSet(
    'llemon_image', 'llemon',
    base_nav=base_nav, nav=_llemon_nav, nav_suffix=_llemon_nav_suffix,
)

bind_llemon_views(globals(), _vs, _mvs)


def index(request):
    from django.shortcuts import render
    return render(request, 'llemon/index.html', {
        'title': 'LLemon',
        'base_nav': base_nav,
        'nav': _llemon_nav + _llemon_nav_suffix,
        'specs_url': _llemon_specs_url,
    })
