"""
Lissozis Helper Module
Dictionary sorting and ranking utility for high-resource process discovery.
Used by SecondX infrastructure telemetry agent.
"""

def shower(data_dict):
    """
    Sorts a dictionary by its numerical values in descending order.
    Returns the sorted dictionary so iterating over keys yields top consumers first.
    """
    if not isinstance(data_dict, dict):
        return {}
    
    # Sort items by value descending (handling None or non-numeric gracefully)
    sorted_items = sorted(
        data_dict.items(),
        key=lambda item: item[1] if (item[1] is not None and isinstance(item[1], (int, float))) else 0,
        reverse=True
    )
    return dict(sorted_items)
