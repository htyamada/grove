from django.urls import reverse_lazy

nav = [
    {'name': 'LLemon',        'url': reverse_lazy('llemon:index')},
    {'name': 'Image Handler', 'url': reverse_lazy('image_handler:index')},
    {'name': 'To Do',         'url': reverse_lazy('to_do_list:index')},
    {'name': 'Media Viewer',  'url': reverse_lazy('mediaview:index')},
]

nav_rel = nav


def specs_nav_item(project):
    return {
        'name': 'Specs',
        'url': f'/zorf/markdown/AUTO/src/hty7/python3/prj/{project}/specs/',
    }
