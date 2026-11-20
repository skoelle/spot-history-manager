(() => {
    const dialog = document.getElementById("lightbox");
    if (!dialog) {
        return;
    }
    const image = document.getElementById("lightbox-image");
    const video = document.getElementById("lightbox-video");
    const caption = document.getElementById("lightbox-caption");
    const closeButton = dialog.querySelector(".lightbox-close");

    const close = () => {
        if (video) {
            video.pause();
            video.removeAttribute("src");
            video.load();
        }
        dialog.close();
    };

    document.addEventListener("click", (event) => {
        const trigger = event.target.closest("[data-photo]");
        if (!trigger) {
            return;
        }
        event.preventDefault();
        const source = trigger.getAttribute("data-photo");
        const type = trigger.getAttribute("data-type") || "image";
        const text = trigger.getAttribute("data-caption") || "";
        if (!source) {
            return;
        }
        if (type === "video") {
            image.style.display = "none";
            image.removeAttribute("src");
            video.style.display = "block";
            video.src = source;
        } else {
            video.pause();
            video.style.display = "none";
            video.removeAttribute("src");
            image.style.display = "block";
            image.src = source;
            image.alt = text;
        }
        caption.textContent = text;
        dialog.showModal();
    });

    closeButton?.addEventListener("click", close);
    dialog.addEventListener("click", (event) => {
        if (event.target === dialog) {
            close();
        }
    });
    dialog.addEventListener("close", () => {
        if (video) {
            video.pause();
        }
    });
})();
