// Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
// Licensed under the MIT License. See LICENSE file in project root for details.
(() => {
    const toggle = document.querySelector(".nav-toggle");
    const nav = document.getElementById("site-nav");
    if (!toggle || !nav) {
        return;
    }

    const close = () => {
        nav.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
    };

    const open = () => {
        nav.classList.add("open");
        toggle.setAttribute("aria-expanded", "true");
    };

    toggle.addEventListener("click", (event) => {
        event.stopPropagation();
        if (nav.classList.contains("open")) {
            close();
        } else {
            open();
        }
    });

    nav.addEventListener("click", (event) => {
        if (event.target.closest("a")) {
            close();
        }
    });

    document.addEventListener("click", (event) => {
        if (!nav.classList.contains("open")) {
            return;
        }
        if (!nav.contains(event.target) && !toggle.contains(event.target)) {
            close();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            close();
        }
    });

    const desktop = window.matchMedia("(min-width: 801px)");
    const onChange = (event) => {
        if (event.matches) {
            close();
        }
    };
    if (desktop.addEventListener) {
        desktop.addEventListener("change", onChange);
    } else {
        desktop.addListener(onChange);
    }
})();
