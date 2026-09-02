from django import template

register = template.Library()


@register.filter
def dict_get(mapping, key):
    """`{{ some_dict|dict_get:some_key }}` -- Django's built-in `foo.bar`
    dict lookup only works for a literal `bar` in the template source, not
    a variable key.
    """
    try:
        return mapping.get(key)
    except AttributeError:
        return None
