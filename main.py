from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, SelectField, IntegerField
from wtforms.validators import Length, InputRequired, DataRequired, Optional
from flask import Flask, render_template, redirect, url_for, session
from flask_bootstrap import Bootstrap5
import requests
from datetime import datetime, date
from places import us_cities, COUNTRY_TO_ISO
import os
# OpenWeatherMap API key


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY")
Bootstrap5(app)

country_choices = [("", "Select a Country")] + [(name, name) for name in COUNTRY_TO_ISO.keys()]

class WeatherQuizForm(FlaskForm):
    country = SelectField(
        "Country",
        choices=country_choices,
        validators=[DataRequired(message="Please select a country.")]
    )

    state = SelectField(
        "State",
        choices=[("", "Select a U.S. State, if any.")] + [(s, s) for s in us_cities.keys()],
        validators=[Optional()]
    )

    city = StringField(
        "City",
        validators=[
            InputRequired(message="Please enter a city."),
            Length(max=50)
        ]
    )

    user_hot = IntegerField(
        "What temperature (°F) do you consider hot?",
        validators=[InputRequired(message="Please enter a temperature.")]
    )

    user_cold = IntegerField(
        "What temperature (°F) do you consider cold?",
        validators=[InputRequired(message="Please enter a temperature.")]
    )

    submit = SubmitField("Get Results")

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False

        city = self.city.data.strip()
        country = self.country.data.strip()
        state = self.state.data.strip() if self.state.data else ""

        # ---------------- USA STATE RULE ----------------
        if country == "United States of America":
            if not state:
                self.state.errors.append("Please select a U.S. state.")
                return False
        else:
            if state:
                self.state.errors.append(
                    "State should only be selected for the United States."
                )
                return False

        # ---------------- TEMPERATURE RULE ----------------
        if self.user_hot.data <= self.user_cold.data:
            self.user_hot.errors.append(
                "Hot temperature must be greater than Cold temperature."
            )
            return False

        # ---------------- USA CITY ↔ STATE CHECK ----------------
        if country == "United States of America":
            valid_cities = us_cities.get(state, [])
            if city not in valid_cities:
                self.city.errors.append(
                    f"{city} is not a valid city in {state}. "
                    "If it is, enter the nearest major city."
                )
                return False

        # ---------------- GEOCODING ----------------
        query = city
        if country == "United States of America":
            query += f", {state}"
        query += f", {country}"

        geocode_url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": query,
            "format": "json",
            "limit": 5,
            "addressdetails": 1
        }
        headers = {"User-Agent": "weather-app"}

        try:
            response = requests.get(
                geocode_url,
                params=params,
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            geo_data = response.json()
        except Exception:
            self.city.errors.append(
                "Error validating location. Please try again."
            )
            return False

        if not geo_data:
            self.city.errors.append(f"{city} not found in {country}.")
            return False

        address = geo_data[0].get("address", {})
        geo_country_code = address.get("country_code", "").lower()
        geo_country_name = address.get("country", "Unknown")
        resolved_city = address.get("city") or address.get("town") or city
        expected_code = COUNTRY_TO_ISO.get(country.strip())

        if country != "United States of America":
            if not expected_code:
                self.city.errors.append(
                    "Country validation configuration error."
                )
                return False

            if geo_country_code != expected_code:
                self.city.errors.append(
                    f"{city} is not a city in {country}. "
                )
                return False

        # ---------------- STORE IN SESSION ----------------
        try:
            session["lat"] = float(geo_data[0]["lat"])
            session["lon"] = float(geo_data[0]["lon"])
            session["city"] = city
            session["country"] = country
            session["state"] = state
        except (KeyError, ValueError):
            self.city.errors.append(
                "Error storing location coordinates."
            )
            return False

        return True
def get_activity_tips(temp_f, description, user_hot=None, user_cold=None):
    """
    Generate user-specific weather activity tips.
    """
    desc = description.lower()
    tips = []

    # Check cold first, then hot, then neutral
    if user_cold is not None and temp_f <= user_cold:
        tips.append("Bundle up/Wear warm clothes")
        tips.append("Limit time outdoors")
    elif user_hot is not None and temp_f >= user_hot:
        tips.append("Stay hydrated")
        tips.append("Avoid peak sun hours")
    else:
        tips.append("Comfortable for outdoor activities")

    # Condition-based tips
    if "rain" in desc:
        tips.append("Bring an umbrella")
    if "snow" in desc:
        tips.append("Drive carefully")
    if "wind" in desc:
        tips.append("Windy — secure loose items")
    if "storm" in desc or "thunder" in desc:
        tips.append("Stay indoors if possible")

    return tips
# -------------------- Forecast --------------------
def get_forecast(lat, lon, api_key, user_hot=None, user_cold=None, max_days=5):
    """
    Fetch 5-day forecast from OpenWeatherMap and include tips based on user thresholds.
    """
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {"lat": lat, "lon": lon, "appid": api_key}

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None

    today = date.today()
    forecasts = {}

    for entry in data.get("list", []):
        dt = datetime.strptime(entry["dt_txt"], "%Y-%m-%d %H:%M:%S")
        dt_date = dt.date()

        # Skip today and duplicates
        if dt_date == today or dt_date in forecasts:
            continue

        temp_k = entry["main"]["temp"]
        temp_f = round((temp_k - 273.15) * 9 / 5 + 32, 1)
        weather = entry["weather"][0]
        description = weather["description"]
        icon = weather.get("icon")

        forecasts[dt_date] = {
            "date": dt_date,
            "temp_f": temp_f,
            "description": description,
            "icon": icon,
            "tips": get_activity_tips(temp_f, description, user_hot, user_cold)
        }

        if len(forecasts) >= max_days:
            break

    return list(forecasts.values())

# -------------------- Routes --------------------
@app.route("/", methods=["GET","POST"])
def home():
    form = WeatherQuizForm()
    if form.validate_on_submit():
        session["user_hot"] = form.user_hot.data
        session["user_cold"] = form.user_cold.data
        return redirect(url_for("results"))
    return render_template("index.html", form=form)

@app.route("/results")
def results():
    lat = session.get("lat")
    lon = session.get("lon")

    if lat is None or lon is None:
        error = "Location could not be validated. Please try again."
        return render_template("results.html", weather=None, error=error)

    forecast = get_forecast(
        lat, lon, os.environ.get("API_KEY"),
        user_hot=session.get("user_hot"),
        user_cold=session.get("user_cold")
    )
    if forecast is None:
        error = "Could not fetch weather data. Try again later."
        return render_template("results.html", weather=None, error=error)

    weather_data = {
        "state": session.get("state"),
        "city": session.get("city"),
        "country": session.get("country"),
        "forecast": forecast
    }

    return render_template(
        "results.html",
        weather=weather_data,
        user_hot=session.get("user_hot"),
        user_cold=session.get("user_cold"),
        error=None
    )

if __name__ == "__main__":
    app.run(debug=False, port=5003)