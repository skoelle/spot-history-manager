(() => {
    const mapElement = document.getElementById("map");
    if (!mapElement || typeof L === "undefined") {
        return;
    }

    const dataElement = document.getElementById("visits-data");
    let visits = [];
    try {
        visits = JSON.parse((dataElement && dataElement.textContent) || "[]");
    } catch (error) {
        visits = [];
    }

    const map = L.map(mapElement, { scrollWheelZoom: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende',
    }).addTo(map);

    const escapeHtml = (value) =>
        String(value == null ? "" : value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");

    const markers = new Map();
    const bounds = [];

    for (const visit of visits) {
        if (visit.lat == null || visit.lng == null) {
            continue;
        }
        const marker = L.circleMarker([visit.lat, visit.lng], {
            radius: 5,
            color: "#0066cc",
            weight: 1,
            fillColor: "#0066cc",
            fillOpacity: 0.65,
        });
        const parts = [
            `<strong>${escapeHtml(visit.place || "Unbekannter Ort")}</strong>`,
            `${escapeHtml(visit.date)} ${escapeHtml(visit.time)}`,
        ];
        if (visit.category) {
            parts.push(escapeHtml(visit.category));
        }
        if (visit.url) {
            parts.push(`<a href="${escapeHtml(visit.url)}" target="_blank" rel="noopener">Original</a>`);
        }
        marker.bindPopup(parts.join("<br>"));
        marker.addTo(map);
        markers.set(String(visit.id), marker);
        bounds.push([visit.lat, visit.lng]);
    }

    if (bounds.length) {
        map.fitBounds(bounds, { padding: [24, 24], maxZoom: 13 });
    } else {
        map.setView([48.137, 11.575], 11);
    }

    document.querySelectorAll(".visit-card[data-id]").forEach((card) => {
        card.addEventListener("click", (event) => {
            if (event.target.closest("a")) {
                return;
            }
            const marker = markers.get(String(card.dataset.id));
            if (!marker) {
                return;
            }
            map.setView(marker.getLatLng(), Math.max(map.getZoom(), 15));
            marker.openPopup();
            card.scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
    });
})();
