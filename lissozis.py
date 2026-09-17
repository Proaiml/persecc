"""
Lissozis Comparison & Ranking Module
Author: İlhan Koçaslan (Proaiml)

Compares all active system processes by their instantaneous resource consumption
(CPU %, RAM %, Disk Read KB/s, Disk Write KB/s) and ranks them to isolate top consumers.
"""

def shower(data_dict):
    """
    Compares all processes within the input dictionary by their resource utilization values.
    Ranks them from highest consumer to lowest consumer in descending order.
    
    Parameters:
        data_dict (dict): Dictionary mapping process names to their metric values
                          (e.g., {'chrome.exe': 15.4, 'python.exe': 8.2, ...})
                          
    Returns:
        dict: Ordered dictionary sorted by value in descending order so that
              iterating yields the highest resource-consuming processes first.
    """
    if not isinstance(data_dict, dict):
        return {}

    def extract_numeric(val):
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    # Compare and sort all processes by metric value descending
    sorted_items = sorted(
        data_dict.items(),
        key=lambda item: extract_numeric(item[1]),
        reverse=True
    )

    return dict(sorted_items)
