from django.shortcuts import render
from .lib.tools import nav as base_nav


def index(request):
    return render(request, 'base/index.html', {
        'title': 'All Agents',
        'base_nav': base_nav,
        'nav': [],
    })
