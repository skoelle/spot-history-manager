(() => {
    const elements = document.querySelectorAll("[data-map-lat][data-map-lng]");
    if (!elements.length || typeof L === "undefined") {
        return;
    }

    const escapeHtml = (value) =>
        String(value == null ? "" : value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");

    for (const element of elements) {
        const lat = Number.parseFloat(element.dataset.mapLat);
        const lng = Number.parseFloat(element.dataset.mapLng);
        if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
            continue;
        }
        const zoom = Number.parseInt(element.dataset.mapZoom || "16", 10);
        const label = element.dataset.mapLabel || "";

        const map = L.map(element, { scrollWheelZoom: false });
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution:
                '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende',
        }).addTo(map);

        const marker = L.circleMarker([lat, lng], {
            radius: 7,
            color: "#0066cc",
            weight: 2,
            fillColor: "#0066cc",
            fillOpacity: 0.7,
        }).addTo(map);
        if (label) {
            marker.bindPopup(`<strong>${escapeHtml(label)}</strong>`);
        }
        map.setView([lat, lng], Number.isFinite(zoom) ? zoom : 16);
        window.setTimeout(() => map.invalidateSize(), 50);
    }
})();
