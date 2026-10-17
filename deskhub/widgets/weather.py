"""Виджет погоды. Данные — Open-Meteo (бесплатно, без ключа)."""
import json
import time

from PySide6.QtCore import QDate, QLocale, QRectF, Qt, QTimer, QUrl, QUrlQuery
from PySide6.QtGui import QColor, QFont
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QInputDialog

from .base import BaseWidget, font, paint_glass
from .weather_icons import describe, draw_weather

REFRESH_MS = 15 * 60 * 1000


class WeatherWidget(BaseWidget):
    name = "weather"
    title = "Погода"
    default_size = (360, 215)

    def __init__(self, config):
        super().__init__(config)
        self.locale = QLocale(QLocale.Russian, QLocale.Russia)
        self.net = QNetworkAccessManager(self)
        self.t0 = time.monotonic()
        self.data = None
        self.place_name = config.data.get("city", "")
        self.status = "Загрузка…"

        self.anim = QTimer(self, interval=40)
        self.anim.timeout.connect(self.update)
        self.anim.start()
        self.refresh_timer = QTimer(self, interval=REFRESH_MS)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        self.refresh()

    # --- сеть ------------------------------------------------------------
    def _get(self, url: str, params: dict, callback):
        u = QUrl(url)
        q = QUrlQuery()
        for k, v in params.items():
            q.addQueryItem(k, str(v))
        u.setQuery(q)
        req = QNetworkRequest(u)
        req.setHeader(QNetworkRequest.UserAgentHeader, "DeskHub/1.0")
        reply = self.net.get(req)

        def done():
            try:
                if reply.error() != QNetworkReply.NoError:
                    self.status = "Нет соединения"
                    QTimer.singleShot(60_000, self.refresh)
                    return
                callback(json.loads(bytes(reply.readAll()).decode("utf-8")))
            except (ValueError, KeyError, IndexError):
                self.status = "Ошибка данных"
            finally:
                reply.deleteLater()

        reply.finished.connect(done)

    def refresh(self):
        city = self.config.data.get("city", "Москва")
        self._get("https://geocoding-api.open-meteo.com/v1/search",
                  {"name": city, "count": 1, "language": "ru", "format": "json"}, self._on_geo)

    def _on_geo(self, js):
        results = js.get("results") or []
        if not results:
            self.status = "Город не найден"
            self.data = None
            return
        g = results[0]
        self.place_name = g.get("name", "")
        self._get("https://api.open-meteo.com/v1/forecast", {
            "latitude": g["latitude"], "longitude": g["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "timezone": "auto", "forecast_days": 4, "wind_speed_unit": "ms",
        }, self._on_forecast)

    def _on_forecast(self, js):
        self.data = js
        self.status = ""

    # --- меню ------------------------------------------------------------
    def extend_menu(self, menu):
        menu.addSeparator()
        menu.addAction("Сменить город…").triggered.connect(self.ask_city)
        menu.addAction("Обновить").triggered.connect(self.refresh)

    def ask_city(self):
        city, ok = QInputDialog.getText(None, "Погода", "Город:", text=self.config.data.get("city", ""))
        if ok and city.strip():
            self.config.data["city"] = city.strip()
            self.config.save()
            self.status = "Загрузка…"
            self.refresh()

    # --- отрисовка -------------------------------------------------------
    def paintEvent(self, _):
        t = time.monotonic() - self.t0
        p = self.begin_paint()
        paint_glass(p, self.glass_rect())

        if not self.data:
            p.setPen(QColor(255, 255, 255, 170))
            p.setFont(font(16))
            p.drawText(self.rect(), Qt.AlignCenter, f"{self.place_name}\n{self.status}")
            p.end()
            return

        cur = self.data["current"]
        kind, desc = describe(cur["weather_code"])
        is_day = bool(cur.get("is_day", 1))

        draw_weather(p, QRectF(14, 14, 110, 110), kind, t, is_day)

        p.setPen(QColor(255, 255, 255))
        p.setFont(font(52, QFont.DemiBold))
        p.drawText(QRectF(134, 12, 210, 66), Qt.AlignLeft | Qt.AlignVCenter, f"{round(cur['temperature_2m'])}°")

        p.setFont(font(15, QFont.Medium))
        p.drawText(QRectF(136, 76, 215, 22), Qt.AlignLeft | Qt.AlignVCenter, desc)
        p.setFont(font(13))
        p.setPen(QColor(255, 255, 255, 140))
        p.drawText(QRectF(136, 98, 215, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   f"Ощущается {round(cur['apparent_temperature'])}° · {cur['wind_speed_10m']:.0f} м/с · "
                   f"{cur['relative_humidity_2m']}%")

        # город в правом верхнем углу
        p.setFont(font(13, QFont.Medium))
        p.setPen(QColor(255, 255, 255, 160))
        p.drawText(QRectF(140, 14, 206, 20), Qt.AlignRight | Qt.AlignVCenter, self.place_name)

        # --- прогноз на 3 дня ---
        p.setPen(QColor(255, 255, 255, 30))
        p.drawLine(18, 134, self.width() - 18, 134)
        daily = self.data["daily"]
        col_w = (self.width() - 24) / 3
        for i in range(1, 4):
            x = 12 + (i - 1) * col_w
            d = QDate.fromString(daily["time"][i], "yyyy-MM-dd")
            label = "Завтра" if i == 1 else self.locale.toString(d, "ddd, d").capitalize()
            dkind, _ = describe(daily["weather_code"][i])
            p.setFont(font(12, QFont.Medium))
            p.setPen(QColor(255, 255, 255, 170))
            p.drawText(QRectF(x, 142, col_w, 18), Qt.AlignHCenter | Qt.AlignVCenter, label)
            draw_weather(p, QRectF(x + col_w / 2 - 34, 160, 34, 34), dkind, t + i, True)
            p.setFont(font(14, QFont.DemiBold))
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(x + col_w / 2 + 2, 160, col_w / 2, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{round(daily['temperature_2m_max'][i])}°")
            p.setFont(font(13))
            p.setPen(QColor(255, 255, 255, 120))
            p.drawText(QRectF(x + col_w / 2 + 2, 177, col_w / 2, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{round(daily['temperature_2m_min'][i])}°")
        p.end()
