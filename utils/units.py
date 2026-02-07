# utils/units.py
"""
Unit conversion utilities for various measurement types.
Supports length, mass, volume, temperature, speed, area, and more.
"""

import logging
import re
from typing import Optional, Dict, Tuple, Any

logger = logging.getLogger(__name__)

# Conversion factors to base units
# Length: base unit = meter
LENGTH_UNITS = {
    # Metric
    'mm': 0.001,
    'millimeter': 0.001,
    'millimeters': 0.001,
    'cm': 0.01,
    'centimeter': 0.01,
    'centimeters': 0.01,
    'm': 1.0,
    'meter': 1.0,
    'meters': 1.0,
    'metre': 1.0,
    'metres': 1.0,
    'km': 1000.0,
    'kilometer': 1000.0,
    'kilometers': 1000.0,
    'kilometre': 1000.0,
    'kilometres': 1000.0,
    # Imperial
    'in': 0.0254,
    'inch': 0.0254,
    'inches': 0.0254,
    'ft': 0.3048,
    'foot': 0.3048,
    'feet': 0.3048,
    'yd': 0.9144,
    'yard': 0.9144,
    'yards': 0.9144,
    'mi': 1609.34,
    'mile': 1609.34,
    'miles': 1609.34,
}

# Mass: base unit = kilogram
MASS_UNITS = {
    # Metric
    'mg': 0.000001,
    'milligram': 0.000001,
    'milligrams': 0.000001,
    'g': 0.001,
    'gram': 0.001,
    'grams': 0.001,
    'kg': 1.0,
    'kilogram': 1.0,
    'kilograms': 1.0,
    't': 1000.0,
    'ton': 1000.0,
    'tons': 1000.0,
    'tonne': 1000.0,
    'tonnes': 1000.0,
    'metric ton': 1000.0,
    'metric tons': 1000.0,
    # Imperial
    'oz': 0.0283495,
    'ounce': 0.0283495,
    'ounces': 0.0283495,
    'lb': 0.453592,
    'lbs': 0.453592,
    'pound': 0.453592,
    'pounds': 0.453592,
}

# Volume: base unit = liter
VOLUME_UNITS = {
    # Metric
    'ml': 0.001,
    'milliliter': 0.001,
    'milliliters': 0.001,
    'millilitre': 0.001,
    'millilitres': 0.001,
    'l': 1.0,
    'liter': 1.0,
    'liters': 1.0,
    'litre': 1.0,
    'litres': 1.0,
    # Imperial
    'tsp': 0.00492892,
    'teaspoon': 0.00492892,
    'teaspoons': 0.00492892,
    'tbsp': 0.0147868,
    'tablespoon': 0.0147868,
    'tablespoons': 0.0147868,
    'fl oz': 0.0295735,
    'fluid ounce': 0.0295735,
    'fluid ounces': 0.0295735,
    'cup': 0.236588,
    'cups': 0.236588,
    'pt': 0.473176,
    'pint': 0.473176,
    'pints': 0.473176,
    'qt': 0.946353,
    'quart': 0.946353,
    'quarts': 0.946353,
    'gal': 3.78541,
    'gallon': 3.78541,
    'gallons': 3.78541,
}

# Speed: base unit = meters per second
SPEED_UNITS = {
    'm/s': 1.0,
    'meters per second': 1.0,
    'metres per second': 1.0,
    'km/h': 0.277778,
    'kmh': 0.277778,
    'kph': 0.277778,
    'kilometers per hour': 0.277778,
    'kilometres per hour': 0.277778,
    'mph': 0.44704,
    'miles per hour': 0.44704,
    'knot': 0.514444,
    'knots': 0.514444,
    'kt': 0.514444,
    'kts': 0.514444,
}

# Area: base unit = square meter
AREA_UNITS = {
    'sq m': 1.0,
    'm2': 1.0,
    'square meter': 1.0,
    'square meters': 1.0,
    'square metre': 1.0,
    'square metres': 1.0,
    'sq km': 1000000.0,
    'km2': 1000000.0,
    'square kilometer': 1000000.0,
    'square kilometers': 1000000.0,
    'square kilometre': 1000000.0,
    'square kilometres': 1000000.0,
    'sq ft': 0.092903,
    'ft2': 0.092903,
    'square foot': 0.092903,
    'square feet': 0.092903,
    'sq mi': 2589988.11,
    'mi2': 2589988.11,
    'square mile': 2589988.11,
    'square miles': 2589988.11,
    'acre': 4046.86,
    'acres': 4046.86,
    'hectare': 10000.0,
    'hectares': 10000.0,
    'ha': 10000.0,
}

# Temperature conversion (special handling required)
TEMPERATURE_UNITS = ['celsius', 'c', 'fahrenheit', 'f', 'kelvin', 'k']


def convert_temperature(value: float, from_unit: str, to_unit: str) -> Optional[float]:
    """
    Convert temperature between Celsius, Fahrenheit, and Kelvin.
    
    Args:
        value: Temperature value
        from_unit: Source unit (celsius/c, fahrenheit/f, kelvin/k)
        to_unit: Target unit (celsius/c, fahrenheit/f, kelvin/k)
    
    Returns:
        Converted temperature value or None if invalid
    """
    from_unit = from_unit.lower()
    to_unit = to_unit.lower()
    
    # Normalize unit names
    if from_unit in ['c', 'celsius']:
        from_celsius = value
    elif from_unit in ['f', 'fahrenheit']:
        from_celsius = (value - 32) * 5/9
    elif from_unit in ['k', 'kelvin']:
        from_celsius = value - 273.15
    else:
        return None
    
    # Convert from Celsius to target
    if to_unit in ['c', 'celsius']:
        return from_celsius
    elif to_unit in ['f', 'fahrenheit']:
        return (from_celsius * 9/5) + 32
    elif to_unit in ['k', 'kelvin']:
        return from_celsius + 273.15
    else:
        return None


def convert_unit(
    value: float,
    from_unit: str,
    to_unit: str
) -> Optional[Dict[str, Any]]:
    """
    Convert a value from one unit to another.
    
    Args:
        value: Value to convert
        from_unit: Source unit (e.g., 'km', 'miles', 'celsius')
        to_unit: Target unit (e.g., 'miles', 'km', 'fahrenheit')
    
    Returns:
        Dictionary with conversion details or None if conversion failed
    """
    from_unit_lower = from_unit.lower().strip()
    to_unit_lower = to_unit.lower().strip()
    
    # Special handling for temperature
    if from_unit_lower in TEMPERATURE_UNITS or to_unit_lower in TEMPERATURE_UNITS:
        result = convert_temperature(value, from_unit_lower, to_unit_lower)
        if result is not None:
            return {
                'original_value': value,
                'original_unit': from_unit,
                'converted_value': round(result, 2),
                'converted_unit': to_unit,
                'category': 'temperature'
            }
        return None
    
    # Check each unit category
    unit_categories = {
        'length': LENGTH_UNITS,
        'mass': MASS_UNITS,
        'volume': VOLUME_UNITS,
        'speed': SPEED_UNITS,
        'area': AREA_UNITS,
    }
    
    for category, units in unit_categories.items():
        if from_unit_lower in units and to_unit_lower in units:
            # Convert to base unit first, then to target unit
            base_value = value * units[from_unit_lower]
            result = base_value / units[to_unit_lower]
            
            return {
                'original_value': value,
                'original_unit': from_unit,
                'converted_value': round(result, 4),
                'converted_unit': to_unit,
                'category': category
            }
    
    logger.warning(f"Cannot convert from {from_unit} to {to_unit}: incompatible or unknown units")
    return None


def format_unit_result(conversion: Dict[str, Any]) -> str:
    """
    Format a unit conversion result for display.
    
    Args:
        conversion: Result from convert_unit()
    
    Returns:
        Formatted string for display
    """
    original = conversion['original_value']
    from_unit = conversion['original_unit']
    converted = conversion['converted_value']
    to_unit = conversion['converted_unit']
    category = conversion['category']
    
    emoji_map = {
        'length': '📏',
        'mass': '⚖️',
        'volume': '🧪',
        'temperature': '🌡️',
        'speed': '🏃',
        'area': '📐'
    }
    
    emoji = emoji_map.get(category, '🔄')
    
    result = f"{emoji} Unit Conversion ({category.title()}):\n"
    result += f"{original:,.4f} {from_unit} = {converted:,.4f} {to_unit}"
    
    return result


def get_supported_units() -> Dict[str, list]:
    """Return dictionary of supported unit categories and their units."""
    return {
        'length': list(set(LENGTH_UNITS.keys())),
        'mass': list(set(MASS_UNITS.keys())),
        'volume': list(set(VOLUME_UNITS.keys())),
        'speed': list(set(SPEED_UNITS.keys())),
        'area': list(set(AREA_UNITS.keys())),
        'temperature': TEMPERATURE_UNITS,
    }
