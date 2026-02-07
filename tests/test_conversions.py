#!/usr/bin/env python3
"""
Tests for currency and unit conversion utilities.
"""

import pytest
import asyncio
from utils.units import (
    convert_unit,
    convert_temperature,
    format_unit_result,
    get_supported_units
)
from utils.conversions import (
    convert_currency,
    format_currency_result,
    get_popular_currencies
)
from agent.skills.conversions import (
    extract_conversion_params,
    is_conversion_query,
    handle_conversion
)


class TestUnitConversion:
    """Test unit conversion functionality."""
    
    def test_length_conversion(self):
        """Test length conversions."""
        # km to miles
        result = convert_unit(5, 'km', 'miles')
        assert result is not None
        assert result['category'] == 'length'
        assert result['original_value'] == 5
        assert abs(result['converted_value'] - 3.1069) < 0.01
        
        # feet to meters
        result = convert_unit(10, 'feet', 'meters')
        assert result is not None
        assert abs(result['converted_value'] - 3.048) < 0.01
    
    def test_mass_conversion(self):
        """Test mass conversions."""
        # pounds to kg
        result = convert_unit(100, 'pounds', 'kg')
        assert result is not None
        assert result['category'] == 'mass'
        assert abs(result['converted_value'] - 45.3592) < 0.01
    
    def test_temperature_conversion(self):
        """Test temperature conversions."""
        # Celsius to Fahrenheit
        result = convert_temperature(25, 'celsius', 'fahrenheit')
        assert result is not None
        assert abs(result - 77.0) < 0.1
        
        # Fahrenheit to Celsius
        result = convert_temperature(32, 'fahrenheit', 'celsius')
        assert result is not None
        assert abs(result - 0.0) < 0.1
        
        # Celsius to Kelvin
        result = convert_temperature(0, 'celsius', 'kelvin')
        assert result is not None
        assert abs(result - 273.15) < 0.1
    
    def test_volume_conversion(self):
        """Test volume conversions."""
        # liters to gallons
        result = convert_unit(10, 'liters', 'gallons')
        assert result is not None
        assert result['category'] == 'volume'
        assert abs(result['converted_value'] - 2.6417) < 0.01
    
    def test_speed_conversion(self):
        """Test speed conversions."""
        # km/h to mph
        result = convert_unit(100, 'km/h', 'mph')
        assert result is not None
        assert result['category'] == 'speed'
        assert abs(result['converted_value'] - 62.1371) < 0.01
    
    def test_incompatible_units(self):
        """Test conversion between incompatible units."""
        result = convert_unit(10, 'kg', 'meters')
        assert result is None
    
    def test_get_supported_units(self):
        """Test getting supported units."""
        units = get_supported_units()
        assert 'length' in units
        assert 'mass' in units
        assert 'volume' in units
        assert 'temperature' in units
        assert 'speed' in units
        assert 'area' in units
    
    def test_format_unit_result(self):
        """Test formatting unit conversion result."""
        result = convert_unit(5, 'km', 'miles')
        formatted = format_unit_result(result)
        assert '📏' in formatted
        assert 'length' in formatted.lower()
        assert '5' in formatted
        assert 'km' in formatted


class TestConversionQuery:
    """Test conversion query detection and parameter extraction."""
    
    def test_is_conversion_query_positive(self):
        """Test positive cases for conversion query detection."""
        assert is_conversion_query("convert 100 USD to EUR")
        assert is_conversion_query("how many miles in 5 km")
        assert is_conversion_query("what is 25 celsius in fahrenheit")
        assert is_conversion_query("50 pounds to kg")
    
    def test_is_conversion_query_negative(self):
        """Test negative cases for conversion query detection."""
        assert not is_conversion_query("what's the weather today")
        assert not is_conversion_query("tell me a joke")
        assert not is_conversion_query("hello how are you")
    
    def test_extract_conversion_params_pattern1(self):
        """Test pattern: convert X FROM_UNIT to TO_UNIT"""
        params = extract_conversion_params("convert 100 USD to EUR")
        assert params is not None
        assert params['value'] == 100
        assert params['from_unit'] == 'usd'
        assert params['to_unit'] == 'eur'
    
    def test_extract_conversion_params_pattern2(self):
        """Test pattern: X FROM_UNIT to TO_UNIT"""
        params = extract_conversion_params("50 pounds to kg")
        assert params is not None
        assert params['value'] == 50
        assert params['from_unit'] == 'pounds'
        assert params['to_unit'] == 'kg'
    
    def test_extract_conversion_params_pattern3(self):
        """Test pattern: what is X FROM_UNIT in TO_UNIT"""
        params = extract_conversion_params("what is 25 celsius in fahrenheit")
        assert params is not None
        assert params['value'] == 25
        assert params['from_unit'] == 'celsius'
        assert params['to_unit'] == 'fahrenheit'
    
    def test_extract_conversion_params_pattern4(self):
        """Test pattern: how many TO_UNIT in X FROM_UNIT"""
        params = extract_conversion_params("how many miles in 5 km")
        assert params is not None
        assert params['value'] == 5
        assert params['from_unit'] == 'km'
        assert params['to_unit'] == 'miles'
    
    def test_extract_conversion_params_with_decimals(self):
        """Test extracting params with decimal values."""
        params = extract_conversion_params("convert 25.5 celsius to fahrenheit")
        assert params is not None
        assert params['value'] == 25.5
    
    def test_extract_conversion_params_with_commas(self):
        """Test extracting params with comma-separated numbers."""
        params = extract_conversion_params("convert 1,000 USD to EUR")
        assert params is not None
        assert params['value'] == 1000


class TestCurrencyConversion:
    """Test currency conversion functionality."""
    
    def test_get_popular_currencies(self):
        """Test getting popular currency list."""
        currencies = get_popular_currencies()
        assert len(currencies) > 0
        assert 'USD' in currencies
        assert 'EUR' in currencies
        assert 'GBP' in currencies
    
    @pytest.mark.asyncio
    async def test_same_currency_conversion(self):
        """Test converting between same currency."""
        result = await convert_currency(100, 'USD', 'USD')
        assert result is not None
        assert result['original_amount'] == 100
        assert result['converted_amount'] == 100
        assert result['exchange_rate'] == 1.0
    
    def test_format_currency_result(self):
        """Test formatting currency conversion result."""
        result = {
            'original_amount': 100,
            'original_currency': 'USD',
            'converted_amount': 85.50,
            'converted_currency': 'EUR',
            'exchange_rate': 0.855
        }
        formatted = format_currency_result(result)
        assert '💱' in formatted
        assert 'USD' in formatted
        assert 'EUR' in formatted
        assert '100' in formatted
        assert '85.50' in formatted


class TestHandleConversion:
    """Test the main handle_conversion function."""
    
    @pytest.mark.asyncio
    async def test_handle_unit_conversion(self):
        """Test handling a unit conversion request."""
        result = await handle_conversion("convert 5 km to miles")
        assert result is not None
        assert 'miles' in result.lower()
    
    @pytest.mark.asyncio
    async def test_handle_temperature_conversion(self):
        """Test handling a temperature conversion request."""
        result = await handle_conversion("what is 25 celsius in fahrenheit")
        assert result is not None
        assert 'fahrenheit' in result.lower() or 'temperature' in result.lower()
    
    @pytest.mark.asyncio
    async def test_handle_invalid_conversion(self):
        """Test handling an invalid conversion request."""
        result = await handle_conversion("this is not a conversion")
        assert result is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
