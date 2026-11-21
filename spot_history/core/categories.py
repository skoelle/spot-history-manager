# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import re
import unicodedata

UNKNOWN_GROUP = "Ohne Kategorie"
SOURCE_CATEGORY = "source"
SOURCE_NAME = "name"

GROUP_ORDER = (
    "Restaurants",
    "Cafés",
    "Biergarten",
    "Bars & Nightlife",
    "Hotels & Unterkünfte",
    "Kultur & Sehenswürdigkeiten",
    "Natur & Outdoor",
    "Sport & Freizeit",
    "Einkaufen",
    "Reisen & Verkehr",
    "Arbeit & Bildung",
    "Events",
    "Sonstiges",
    UNKNOWN_GROUP,
)

GROUP_RANK = {group: index for index, group in enumerate(GROUP_ORDER)}

_CATEGORY_GROUPS = {
    "Restaurants": (
        "African Restaurant",
        "American Restaurant",
        "Asian Restaurant",
        "Austrian Restaurant",
        "BBQ Joint",
        "Bavarian Restaurant",
        "Bistro",
        "Brasserie",
        "Breakfast Spot",
        "Burger Joint",
        "Cajun and Creole Restaurant",
        "Chinese Restaurant",
        "Currywurst Joint",
        "Diner",
        "Doner Restaurant",
        "Eastern European Restaurant",
        "Ethiopian Restaurant",
        "Fast Food Restaurant",
        "French Restaurant",
        "Fried Chicken Joint",
        "Gastropub",
        "German Restaurant",
        "Greek Restaurant",
        "Grilled Meat Restaurant",
        "Indian Restaurant",
        "Italian Restaurant",
        "Japanese Restaurant",
        "Jewish Restaurant",
        "Malga",
        "Mediterranean Restaurant",
        "Mexican Restaurant",
        "Middle Eastern Restaurant",
        "Mongolian Restaurant",
        "Peruvian Restaurant",
        "Pizzeria",
        "Portuguese Restaurant",
        "Restaurant",
        "Russian Restaurant",
        "Salad Restaurant",
        "Scandinavian Restaurant",
        "Seafood Restaurant",
        "Snack Place",
        "Southern Food Restaurant",
        "Spanish Restaurant",
        "Sushi Restaurant",
        "Taco Restaurant",
        "Tapas Restaurant",
        "Taverna",
        "Thai Restaurant",
        "Theme Restaurant",
        "Trattoria",
        "Vegan and Vegetarian Restaurant",
        "Vietnamese Restaurant",
    ),
    "Cafés": (
        "Bakery",
        "Café",
        "Coffee Roaster",
        "Coffee Shop",
        "Corporate Coffee Shop",
        "Dessert Shop",
        "Donut Shop",
        "Ice Cream Parlor",
        "Pastry Shop",
    ),
    "Biergarten": ("Beer Garden",),
    "Bars & Nightlife": (
        "Bar",
        "Beach Bar",
        "Beer Bar",
        "Brewery",
        "Cocktail Bar",
        "Dive Bar",
        "Gay Bar",
        "Hotel Bar",
        "Irish Pub",
        "Jazz and Blues Venue",
        "Lounge",
        "Music Venue",
        "Night Club",
        "Pool Hall",
        "Pub",
        "Sports Bar",
        "Wine Bar",
    ),
    "Hotels & Unterkünfte": (
        "Bed and Breakfast",
        "Boarding House",
        "Campground",
        "Hostel",
        "Hotel",
        "Motel",
        "Mountain Hut",
        "Resort",
        "RV Park",
        "Ski Lodge",
    ),
    "Kultur & Sehenswürdigkeiten": (
        "Art Gallery",
        "Art Museum",
        "Arts and Entertainment",
        "Bridge",
        "Castle",
        "Cemetery",
        "Church",
        "Concert Hall",
        "Cultural Center",
        "Fountain",
        "Historic and Protected Site",
        "History Museum",
        "Indie Movie Theater",
        "Landmark Building",
        "Library",
        "Lighthouse",
        "Memorial Site",
        "Monastery",
        "Monument",
        "Mosque",
        "Movie Theater",
        "Museum",
        "Opera House",
        "Palace",
        "Performing Arts Venue",
        "Plaza",
        "Science Museum",
        "Sculpture Garden",
        "Structure",
        "Theater",
        "Tower",
    ),
    "Natur & Outdoor": (
        "Bathing Area",
        "Beach",
        "Botanical Garden",
        "Cape",
        "Dog Park",
        "Farm",
        "Field",
        "Forest",
        "Garden",
        "Hiking Trail",
        "Island",
        "Lake",
        "Landmarks and Outdoors",
        "Mountain",
        "Other Great Outdoors",
        "Park",
        "Pier",
        "Playground",
        "River",
        "Scenic Lookout",
        "State or Provincial Park",
        "Vineyard",
        "Waterfall",
    ),
    "Sport & Freizeit": (
        "Amusement Park",
        "Aquarium",
        "Arcade",
        "Bowling Alley",
        "Climbing Gym",
        "Equestrian Facility",
        "Gym",
        "Gym and Studio",
        "Race Track",
        "Rock Climbing Spot",
        "Skate Park",
        "Ski Resort and Area",
        "Soccer Stadium",
        "Sports and Recreation",
        "Stadium",
        "Surf Spot",
        "Swimming Pool",
        "Tennis Court",
        "Water Park",
        "Zoo",
    ),
    "Einkaufen": (
        "Baby Store",
        "Bookstore",
        "Car Dealership",
        "Cheese Store",
        "Children's Clothing Store",
        "Clothing Store",
        "Convenience Store",
        "Department Store",
        "Electronics Store",
        "Farmers Market",
        "Fish Market",
        "Food and Beverage Retail",
        "Furniture and Home Store",
        "Garden Center",
        "Hardware Store",
        "Liquor Store",
        "Lingerie Store",
        "Market",
        "Organic Grocery",
        "Outdoor Supply Store",
        "Outlet Mall",
        "Shopping Mall",
        "Sporting Goods Retail",
        "Supermarket",
        "Toy Store",
        "Vintage and Thrift Store",
        "Wine Store",
    ),
    "Reisen & Verkehr": (
        "Airport",
        "Airport Gate",
        "Airport Terminal",
        "Boat or Ferry",
        "Cable Car",
        "Fuel Station",
        "Harbor or Marina",
        "International Airport",
        "Light Rail Station",
        "Parking",
        "Platform",
        "Rail Station",
        "Rental Car Location",
        "Rest Area",
        "Toll Plaza",
        "Train",
        "Tram Station",
        "Tourist Information and Service",
        "Travel and Transportation",
        "Truck Stop",
    ),
    "Arbeit & Bildung": (
        "Business and Professional Services",
        "College Auditorium",
        "College Communications Building",
        "College Engineering Building",
        "College Gym",
        "College Theater",
        "College and University",
        "Coworking Space",
        "Design Studio",
        "Education",
        "Elementary School",
        "Industrial Estate",
        "Office",
        "Radio Station",
        "TV Station",
        "Tech Startup",
        "University",
    ),
    "Events": (
        "Beer Festival",
        "Christmas Market",
        "Conference",
        "Convention Center",
        "Event Space",
        "Fair",
        "Festival",
        "Music Festival",
        "Parade",
        "Street Fair",
    ),
    "Sonstiges": (
        "Animal Shelter",
        "Bank",
        "Car Wash and Detail",
        "City",
        "City Hall",
        "Courthouse",
        "Fire Station",
        "Home (private)",
        "Hospital",
        "Neighborhood",
        "Optometrist",
        "Post Office",
        "Town",
        "Village",
    ),
}

_EXACT_GROUPS = {
    category: group
    for group, categories in _CATEGORY_GROUPS.items()
    for category in categories
}
_EXACT_FOLDED = {
    category.casefold(): group for category, group in _EXACT_GROUPS.items()
}

_CATEGORY_RULES = (
    (re.compile(r"restaurant$", re.IGNORECASE), "Restaurants"),
    (re.compile(r"(?:^|\s)(?:bar|pub|lounge|club)$", re.IGNORECASE), "Bars & Nightlife"),
    (re.compile(r"museum$", re.IGNORECASE), "Kultur & Sehenswürdigkeiten"),
    (re.compile(r"(?:theater|gallery|church|mosque|temple)$", re.IGNORECASE), "Kultur & Sehenswürdigkeiten"),
    (re.compile(r"(?:store|shop|market)$", re.IGNORECASE), "Einkaufen"),
    (re.compile(r"(?:park|garden)$", re.IGNORECASE), "Natur & Outdoor"),
    (re.compile(r"(?:hotel|hostel|motel|resort)$", re.IGNORECASE), "Hotels & Unterkünfte"),
    (re.compile(r"(?:gym|stadium|pool|beach)$", re.IGNORECASE), "Sport & Freizeit"),
    (re.compile(r"(?:airport|station|terminal|platform)$", re.IGNORECASE), "Reisen & Verkehr"),
    (re.compile(r"(?:school|college|university|office)$", re.IGNORECASE), "Arbeit & Bildung"),
)

_NAME_RULES = (
    (re.compile(r"\b(?:biergarten|beer\s*garden)\b", re.IGNORECASE), "Biergarten"),
    (
        re.compile(
            r"caf[eé]|\bkafe\b|kaffee|coffee|\bbäckerei\b|\bbakery\b|"
            r"patisserie|\bstarbucks\b",
            re.IGNORECASE,
        ),
        "Cafés",
    ),
    (
        re.compile(
            r"\brestaurant\b|\bpizzeria\b|\btrattoria\b|\bosteria\b|"
            r"\bwirtshaus\b|\bgasthof\b|\bgasthaus\b|\bgaststätte\b|"
            r"\bgaststaette\b|stüberl|\bstube(?:n)?\b|\bkebap\b|"
            r"\bimbiss\b|\bsteakhouse\b|\bburger\b|\bsushi\b|\btapas\b|"
            r"\bmeze\b|döner|\bdoener\b|\bgrill\b|\bküche\b|"
            r"\bkitchen\b|\bbrasserie\b|\bbistro\b|keller|"
            r"wirtschaft|\bbrauhaus\b",
            re.IGNORECASE,
        ),
        "Restaurants",
    ),
    (
        re.compile(
            r"(?:^|\s)(?:bar|pub|club|lounge|kneipe)\b|\bbrauerei\b|"
            r"\bbrewery\b",
            re.IGNORECASE,
        ),
        "Bars & Nightlife",
    ),
    (
        re.compile(
            r"\bhotel\b|\bhostel\b|\bpension\b|\bresort\b|"
            r"\bferienwohnung\b|\bapartment\b",
            re.IGNORECASE,
        ),
        "Hotels & Unterkünfte",
    ),
    (
        re.compile(
            r"museum|\bgalerie\b|\bgallery\b|\btheater\b|\bkirche\b|"
            r"\bchurch\b|schloss|\bburg\b|\bpalast\b|\bpalace\b|"
            r"denkmal|\bmonument\b|\bturm\b|\btower\b|bibliothek|"
            r"\blibrary\b|\bkunst\b|\bkonzert\b|\bconcert\b|\boper\b|"
            r"\bcamii?\b|\bkapelle\b",
            re.IGNORECASE,
        ),
        "Kultur & Sehenswürdigkeiten",
    ),
    (
        re.compile(
            r"flughafen|\bairport\b|bahnhof|\bstation\b|\bhafen\b|"
            r"\bharbor\b|\bmarina\b|tankstelle|\bfuel\b|parkplatz|"
            r"\bparking\b",
            re.IGNORECASE,
        ),
        "Reisen & Verkehr",
    ),
    (
        re.compile(
            r"\bfestival\b|\bfest\b|festzelt|festhalle|weihnachtsmarkt|"
            r"\bmesse\b|\bconvention\b|\bevent\b",
            re.IGNORECASE,
        ),
        "Events",
    ),
    (
        re.compile(
            r"\bgym\b|schwimmbad|\bpool\b|\bbowling\b|minigolf|"
            r"\btennis\b|stadion|\bstadium\b|\bzoo\b|tierpark|"
            r"\baquarium\b|\bfitness\b|\bsport\b",
            re.IGNORECASE,
        ),
        "Sport & Freizeit",
    ),
    (
        re.compile(
            r"\b(?:shop|store)\b|markt|market|supermarkt|"
            r"\bsupermarket\b|\bgeschäft\b|\bladen\b|\bboutique\b|"
            r"\bmall\b|gartencenter",
            re.IGNORECASE,
        ),
        "Einkaufen",
    ),
    (
        re.compile(
            r"park|garten|garden|\bsee\b|\bwald\b|"
            r"\bforest\b|\bstrand\b|\bbeach\b|\bberg\b|\bmountain\b|"
            r"\bfluss\b|\briver\b|\binsel\b|\bisland\b|wasserfall|"
            r"\bwaterfall\b|\bufer\b",
            re.IGNORECASE,
        ),
        "Natur & Outdoor",
    ),
    (
        re.compile(
            r"\bschule\b|\bschool\b|universität|\buniversity\b|"
            r"hochschule|\bcollege\b|\bbüro\b|\boffice\b|coworking",
            re.IGNORECASE,
        ),
        "Arbeit & Bildung",
    ),
)


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def group_for_category(category: str | None) -> str | None:
    if not category:
        return None
    text = _normalize(category)
    exact = _EXACT_GROUPS.get(category)
    if exact:
        return exact
    if text in _EXACT_FOLDED:
        return _EXACT_FOLDED[text]
    for pattern, group in _CATEGORY_RULES:
        if pattern.search(text):
            return group
    return None


def group_for_name(name: str | None) -> str | None:
    text = _normalize(name)
    if not text:
        return None
    for pattern, group in _NAME_RULES:
        if pattern.search(text):
            return group
    return None


def resolve_category_group(
    category: str | None, name: str | None = None
) -> tuple[str, str | None]:
    if category:
        group = group_for_category(category)
        if group:
            return group, SOURCE_CATEGORY
    inferred = group_for_name(name)
    if inferred:
        return inferred, SOURCE_NAME
    if category:
        return "Sonstiges", SOURCE_CATEGORY
    return UNKNOWN_GROUP, None
