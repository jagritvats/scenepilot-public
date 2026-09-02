"""Deterministic evidence heuristics: freshness from dates, authority from source domain."""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlparse

from ..domain.enums import Authority, Freshness

OFFICIAL_HINTS = (".gov", ".gov.in", ".nic.in", ".res.in", "tropmet", "imd.", ".mil.in", "india.gov", "dgca", "mausam.imd", "imd.gov", "mumbaipolice", ".mil", "europa.eu", "who.int", "digitalsky", "mcgm", "portal.mcgm", "bmc.gov", "mumbaicity", "ndma", "mahapolice")
NEWS_HINTS = ("timesofindia", "hindustantimes", "indianexpress", "thehindu", "ndtv", "bbc.", "reuters", "apnews", "theguardian", "nytimes", "mid-day", "livemint", "economictimes", "indiatoday", "news18", "scroll.in", "theprint", "deccanherald", "freepressjournal", "financialexpress", "business-standard", "cnn.", "bloomberg", "aljazeera", "dnaindia", "firstpost", "thewire", "newindianexpress", "telegraphindia", "tribuneindia", "moneycontrol", "hindustan", "mumbaimirror", "skymetweather", "accuweather", "weather.com", "timeanddate")
INDUSTRY_HINTS = ("filmfacilitation", "ffo.gov", "variety", "hollywoodreporter", "deadline", "screendaily", "filmcompanion", "productionhub", "kftpc", "mumbaifilmcity", "filmcity", "dji", "cinematography", "nofilmschool", "premiumbeat", "backstage", "stagepool", "britishcinematographer", "thelocationguide", "kodak", "arri", "ascmag", "setlighting", "cinema5d", "newsshooter", "provideocoalition", "filmmaker", "indiewire", "drone", "uav", "aerial")
COMMUNITY_HINTS = ("reddit", "quora", "medium.com", "blogspot", "wordpress", "facebook", "instagram", "twitter", "x.com", "youtube", "tumblr", "linkedin", "stackexchange", "tripadvisor", "pinterest", "substack")


def authority_for(url: str) -> Authority:
    host = (urlparse(url).netloc or url).lower()
    if any(h in host for h in OFFICIAL_HINTS):
        return Authority.OFFICIAL
    if any(h in host for h in NEWS_HINTS):
        return Authority.NEWS
    if any(h in host for h in COMMUNITY_HINTS):
        return Authority.COMMUNITY
    if any(h in host for h in INDUSTRY_HINTS):
        return Authority.INDUSTRY
    return Authority.UNKNOWN


def freshness_for(publish_date: str | None, today: date | None = None) -> Freshness:
    if not publish_date:
        return Freshness.UNKNOWN
    try:
        d = datetime.strptime(publish_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return Freshness.UNKNOWN
    today = today or date.today()
    age = (today - d).days
    if age <= 90:
        return Freshness.CURRENT
    if age <= 365:
        return Freshness.RECENT
    return Freshness.DATED


AUTHORITY_CONF = {Authority.OFFICIAL: 0.95, Authority.NEWS: 0.8, Authority.INDUSTRY: 0.7, Authority.UNKNOWN: 0.5, Authority.COMMUNITY: 0.4}
FRESHNESS_CONF = {Freshness.CURRENT: 1.0, Freshness.RECENT: 0.9, Freshness.UNKNOWN: 0.8, Freshness.DATED: 0.6}


def combined_confidence(model_confidence: float, authority: Authority, freshness: Freshness, relevance: float) -> float:
    """Blend Gemini's judgement with deterministic source heuristics."""
    heuristic = AUTHORITY_CONF[authority] * FRESHNESS_CONF[freshness]
    return round(max(0.0, min(1.0, 0.5 * model_confidence + 0.3 * heuristic + 0.2 * relevance)), 2)
