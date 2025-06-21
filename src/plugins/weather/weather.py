from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image
import os
import requests
import logging
import datetime
import pytz
from io import BytesIO
import math

import openmeteo_requests

import pandas as pd
import requests_cache
from retry_requests import retry

# Setup the Open-Meteo API client with cache and retry on error                                                         #Evtl. muss das wo anders hin
cache_session = requests_cache.CachedSession('.cache', expire_after = 3600)
retry_session = retry(cache_session, retries = 5, backoff_factor = 0.2)
openmeteo = openmeteo_requests.Client(session = retry_session)

logger = logging.getLogger(__name__)

# Evtl. bis auf Metric entfernen, wenns keine Probleme macht
UNITS = {
    "standard": {
        "temperature": "K",
        "speed": "m/s"
    },
    "metric": {
        "temperature": "°C",
        "speed": "km/h"

    },
    "imperial": {
        "temperature": "°F",
        "speed": "mph"
    }
}

wochentage = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
wochentage_kurz = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
monate = ["Januar", "Februar", "März", "April", "Mai", "Juni",
          "Juli", "August", "September", "Oktober", "November", "Dezember"]

#Nicht mehr notwendig
'''
WEATHER_URL = "https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={long}&units={units}&exclude=minutely&appid={api_key}"
AIR_QUALITY_URL = "http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={long}&appid={api_key}"
GEOCODING_URL = "http://api.openweathermap.org/geo/1.0/reverse?lat={lat}&lon={long}&limit=1&appid={api_key}"
'''

class Weather(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": False,                                                                                          #Changed to False weil nicht notwendig, falls Probleme zurückändern
            "service": "OpenMeteo",
            "expected_key": "OPEN_WEATHER_MAP_SECRET"
        }
        template_params['style_settings'] = True

        return template_params

    def generate_image(self, settings, device_config):
        '''api_key = device_config.load_env_key("OPEN_WEATHER_MAP_SECRET")
        if not api_key:
            raise RuntimeError("Open Weather Map API Key not configured.")'''                                                                                                       #API-Key nicht mehr notwendig, bei Problemen wieder einblenden

        lat = settings.get('latitude')
        long = settings.get('longitude')
        if not lat or not long:
            raise RuntimeError("Latitude and Longitude are required.")

        units = settings.get('units')
        if not units or units not in ['metric', 'imperial', 'standard']:
            raise RuntimeError("Units are required.")

        try:
            dwd_data, hourly_dwd_data, daily_dwd_data = self.get_DWD_data(lat, long)
            rest_data, hourly_rest_data, daily_rest_data = self.get_rest_data(lat, long)
            aqi_data = self.get_AQI_data(lat, long)
            #Nicht mehr notwendig
            '''weather_data = self.get_weather_data(api_key, units, lat, long)
            aqi_data = self.get_air_quality(api_key, lat, long)
            location_data = self.get_location(api_key, lat, long)'''
        except Exception as e:
            logger.error(f"Failed to make Weather data request: {str(e)}")
            raise RuntimeError("Weather data request failure, please check logs.")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        #timezone = device_config.get_config("timezone", default="America/New_York")                                    #Erfordert umschreiben von tz in parse_weather_data
        #tz = pytz.timezone(timezone)
        # Template Params ist die Variable, die so wie im Beispiel aussehen muss, also muss data in parse Weather data angepasst werden bzw. parse weather data umgeschrieben werden
        template_params = self.parse_weather_data(dwd_data, hourly_dwd_data, daily_dwd_data, rest_data, hourly_rest_data, daily_rest_data, aqi_data, units)

        template_params["plugin_settings"] = settings

        image = self.render_image(dimensions, "weather.html", "weather.css", template_params)

        if not image:
            raise RuntimeError("Failed to take screenshot, please check logs.")
        return image


    def parse_weather_data(self, dwd_weather_data, hourly_dwd_data, daily_dwd_data, rest_data, hourly_rest_data, daily_rest_data, aqi_data, units):
        dt = datetime.datetime.fromtimestamp(dwd_weather_data["Current Time"])
        wochentag = wochentage[dt.weekday()]        # .weekday() gibt 0 (Montag) bis 6 (Sonntag)
        monat = monate[dt.month - 1]                # Monate sind 1-basiert
        current_icon = int(dwd_weather_data["Current weather code"])
        data = {
            "current_date": f"{wochentag}, {dt.day}. {monat}",
            "location": "Affaltrach", #location_str,
            "current_day_icon": self.get_plugin_dir(f'icons/{current_icon}.png'),
            "current_temperature": str(round(dwd_weather_data["Current Temp"], 1)),
            "feels_like": str(round(dwd_weather_data["Current app Temp"], 1)),
            "temperature_unit": UNITS[units]["temperature"],
            "units": units
        }

        forecast = []
        for i in range(7):
            zeitstempel = daily_dwd_data.iloc[i,0]
            day_name = wochentage_kurz[zeitstempel.weekday()]
            #daily_temp_max = daily_dwd_data.iloc[i,2]
            #daily_temp_min = daily_dwd_data.iloc[i,3]
            weather_icon = daily_dwd_data.iloc[i,1]
            weather_icon_path = int(self.get_plugin_dir(f"icons/{weather_icon}.png"))

            forecast.append(
                {
                    "day": day_name,
                    "high": int(daily_dwd_data.iloc[i,2]),
                    "low": int(daily_dwd_data.iloc[i,3]),
                    "icon": weather_icon_path,
                    "moon_phase_pct": "50",
                    "moon_phase_icon": "/usr/local/inkypi/src/plugins/weather/icons/firstquarter.png",
                }
            )
        data['forecast'] = forecast                                                                                     #daily forecast

        data_points = []

        sunrise_dt = datetime.datetime.fromtimestamp(daily_dwd_data.iloc[0,4])
        data_points.append({
            "label": "Sonnenaufgang",
            "measurement": sunrise_dt.strftime('%H:%M').lstrip("0"),
            "unit": "",
            "icon": self.get_plugin_dir('icons/sunrise.png')
        })

        sunset_dt = datetime.datetime.fromtimestamp(daily_dwd_data.iloc[0,5])
        data_points.append({
            "label": "Sonnenuntergang",
            "measurement": sunset_dt.strftime('%H:%M').lstrip("0"),
            "unit": "",
            "icon": self.get_plugin_dir('icons/sunset.png')
        })

        data_points.append({
            "label": "Wind",
            "measurement": round(daily_dwd_data.iloc[0,6], 2),
            "unit": UNITS[units]["speed"],
            "icon": self.get_plugin_dir('icons/wind.png')
        })

        data_points.append({
            "label": "Luftfeutigkeit",
            "measurement": round(dwd_weather_data["Current rel Humidity"]),
            "unit": '%',
            "icon": self.get_plugin_dir('icons/humidity.png')
        })

        data_points.append({
            "label": "Luftdruck",
            "measurement": round(dwd_weather_data["Current rel Humidity"]),
            "unit": 'hPa',
            "icon": self.get_plugin_dir('icons/pressure.png')
        })

        data_points.append({
            "label": "UV Index",
            "measurement": round(daily_rest_data.iloc[0,1]),
            "unit": '',
            "icon": self.get_plugin_dir('icons/uvi.png')
        })

        visibility = round(daily_rest_data.iloc[0,2] / 1000)
        visibility_str = f"{visibility}" #if visibility >= 10 else visibility
        data_points.append({
            "label": "Sichtweite",
            "measurement": visibility_str,
            "unit": 'km',
            "icon": self.get_plugin_dir('icons/visibility.png')
        })

        aqi = round(aqi_data["Current_AQI"])
        aqi = math.ceil(aqi/20)
        data_points.append({
            "label": "Luftqualität",
            "measurement": aqi,
            "unit": ["Sehr Gut", "Gut", "Mittelmäßig", "Schlecht", "Sehr Schlecht", "Extrem Schlecht", "Extrem Schlecht", "Extrem Schlecht"][int(aqi) - 1],
            "icon": self.get_plugin_dir('icons/aqi.png')
        })

        data['data_points'] = data_points                                                                               #current forecast

        hourly = []
        dt_hour_offset = datetime.datetime.fromtimestamp(dwd_weather_data["Current Time"])
        dt_hour_offset_int = int(f"{dt_hour_offset:%H}")
        for i in range(24):
            zeitstempel = hourly_dwd_data.iloc[i+dt_hour_offset_int, 0]
            dt = zeitstempel.hour
            hour_forecast = {
                "time": str(dt),
                "temperature": int(hourly_dwd_data.iloc[i+dt_hour_offset_int, 1]),
                "precipitiation": (hourly_rest_data.iloc[i+dt_hour_offset_int, 1])/100,
            }
            hourly.append(hour_forecast)

        data['hourly_forecast'] = hourly                                                                                #hourly forecast
        #print(data.json())
        return data


    def get_DWD_data(self, lat, long):
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": long,
            "daily": ["weather_code", "temperature_2m_max", "temperature_2m_min", "sunrise", "sunset",
                      "wind_speed_10m_max"],
            "hourly": "temperature_2m",
            "models": "icon_seamless",
            "current": ["temperature_2m", "relative_humidity_2m", "apparent_temperature", "pressure_msl",
                        "weather_code"],
            "timezone": "auto"
        }
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        current = response.Current()
        hourly = response.Hourly()
        hourly_temperature_2m = hourly.Variables(0).ValuesAsNumpy()

        hourly_data = {"date": pd.date_range(
            start=pd.to_datetime(hourly.Time()+response.UtcOffsetSeconds(), unit="s", utc=True),
            end=pd.to_datetime(hourly.TimeEnd()+response.UtcOffsetSeconds(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left"
        )}
        hourly_data["temperature_2m"] = hourly_temperature_2m
        hourly_dataframe = pd.DataFrame(data=hourly_data)

        daily = response.Daily()
        daily_weather_code = daily.Variables(0).ValuesAsNumpy()
        daily_temperature_2m_max = daily.Variables(1).ValuesAsNumpy()
        daily_temperature_2m_min = daily.Variables(2).ValuesAsNumpy()
        daily_sunrise = daily.Variables(3).ValuesInt64AsNumpy()
        daily_sunset = daily.Variables(4).ValuesInt64AsNumpy()
        daily_wind_speed_10m_max = daily.Variables(5).ValuesAsNumpy()
        daily_data = {"date": pd.date_range(
            start=pd.to_datetime(daily.Time()+response.UtcOffsetSeconds(), unit="s", utc=True),
            end=pd.to_datetime(daily.TimeEnd()+response.UtcOffsetSeconds(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=daily.Interval()),
            inclusive="left"
        )}
        daily_data["weather_code"] = daily_weather_code
        daily_data["temperature_2m_max"] = daily_temperature_2m_max
        daily_data["temperature_2m_min"] = daily_temperature_2m_min
        daily_data["sunrise"] = daily_sunrise
        daily_data["sunset"] = daily_sunset
        daily_data["wind_speed_10m_max"] = daily_wind_speed_10m_max
        daily_dataframe = pd.DataFrame(data=daily_data)

        dwd_data = {
            "Coordinates" : [response.Latitude(), response.Longitude()],
            "Elevation" : response.Elevation(),
            "Timezone" : response.Timezone(),
            "Timezone_offset" : response.UtcOffsetSeconds(),

            "Current Time" : current.Time(),
            "Current Temp" : current.Variables(0).Value(),
            "Current rel Humidity" : current.Variables(1).Value(),
            "Current app Temp" : current.Variables(2).Value(),
            "Current pressure msl" : current.Variables(3).Value(),
            "Current weather code" : current.Variables(4).Value(),


        }
        return dwd_data, hourly_dataframe, daily_dataframe

    def get_rest_data(self, lat, long):
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": long,
            "daily": ["uv_index_max", "visibility_max"],
            "hourly": "precipitation_probability",
            "timezone": "auto",
            "forecast_days": 2
        }
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        rest_data = {
            "Coordinates": [response.Latitude(), response.Longitude()],
            "Elevation": response.Elevation(),
            "Timezone": response.Timezone(),
            "Timezone_offset": response.UtcOffsetSeconds(),
        }

        hourly = response.Hourly()
        hourly_precipitation_probability = hourly.Variables(0).ValuesAsNumpy()
        hourly_data = {"date": pd.date_range(
            start=pd.to_datetime(hourly.Time()+response.UtcOffsetSeconds(), unit="s", utc=True),
            end=pd.to_datetime(hourly.TimeEnd()+response.UtcOffsetSeconds(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left"
        )}
        hourly_data["precipitation_probability"] = hourly_precipitation_probability
        hourly_dataframe = pd.DataFrame(data=hourly_data)

        daily = response.Daily()
        daily_uv_index_max = daily.Variables(0).ValuesAsNumpy()
        daily_visibility_max = daily.Variables(1).ValuesAsNumpy()
        daily_data = {"date": pd.date_range(
            start=pd.to_datetime(daily.Time()+response.UtcOffsetSeconds(), unit="s", utc=True),
            end=pd.to_datetime(daily.TimeEnd()+response.UtcOffsetSeconds(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=daily.Interval()),
            inclusive="left"
        )}
        daily_data["uv_index_max"] = daily_uv_index_max
        daily_data["visibility_max"] = daily_visibility_max
        daily_dataframe = pd.DataFrame(data=daily_data)

        return rest_data, hourly_dataframe, daily_dataframe

    def get_AQI_data(self, lat, long):
        url = "https://air-quality-api.open-meteo.com/v1/air-quality"
        params = {
            "latitude": lat,
            "longitude": long,
            "current": "european_aqi",
            "timezone": "auto",
            "forecast_days": 1
        }
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        current = response.Current()
        #current_european_aqi = current.Variables(0).Value()
        aqi_data = {
            "Coordinates": [response.Latitude(), response.Longitude()],
            "Elevation": response.Elevation(),
            "Timezone": response.Timezone(),
            "Timezone_offset": response.UtcOffsetSeconds(),
            "Current_AQI":  current.Variables(0).Value(),
        }
        return aqi_data


# Alte Wetter-API Abfragen
'''  
    def get_weather_data(self, api_key, units, lat, long):
        url = WEATHER_URL.format(lat=lat, long=long, units=units, api_key=api_key)
        response = requests.get(url)
        if not 200 <= response.status_code < 300:
            logging.error(f"Failed to retrieve weather data: {response.content}")
            raise RuntimeError("Failed to retrieve weather data.")

        return response.json()

    def get_air_quality(self, api_key, lat, long):
        url = AIR_QUALITY_URL.format(lat=lat, long=long, api_key=api_key)
        response = requests.get(url)

        if not 200 <= response.status_code < 300:
            logging.error(f"Failed to get air quality data: {response.content}")
            raise RuntimeError("Failed to retrieve air quality data.")

        return response.json()

    def get_location(self, api_key, lat, long):
        url = GEOCODING_URL.format(lat=lat, long=long, api_key=api_key)
        response = requests.get(url)

        if not 200 <= response.status_code < 300:
            logging.error(f"Failed to get location: {response.content}")
            raise RuntimeError("Failed to retrieve location.")

        return response.json()[0]
'''
