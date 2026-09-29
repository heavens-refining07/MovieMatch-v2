/**
 * MovieMatch Swipe Physics Controller
 * Handles touch gestures, mouse dragging, stamp opacities, and swipe animations.
 */
function cardEscape(value) {
  const node = document.createElement("span");
  node.textContent = value ?? "";
  return node.innerHTML;
}

function safePosterUrl(value) {
  return typeof value === "string" && value.startsWith("https://")
    ? value
    : "https://placehold.co/500x750/181824/f6f3ff?text=No+Poster";
}

class CardSwipeController {
  constructor(container, onSwipeCallback) {
    this.container = container;
    this.onSwipeCallback = onSwipeCallback;
    this.cards = [];
    this.currentCard = null;
    this.isDragging = false;
    this.startX = 0;
    this.startY = 0;
    this.currentX = 0;
    this.currentY = 0;
    this.threshold = 85;
    this.boundPointerDown = this.handlePointerDown.bind(this);
    this.boundPointerMove = this.handlePointerMove.bind(this);
    this.boundPointerUp = this.handlePointerUp.bind(this);
  }

  init(cardsList) {
    this.cards = cardsList;
    this.renderStack();
  }

  renderStack() {
    this.container.innerHTML = "";
    if (!this.cards || this.cards.length === 0) {
      return;
    }

    const visibleCards = this.cards.slice(0, 3).reverse();
    
    visibleCards.forEach((movie, index) => {
      const isTop = (index === visibleCards.length - 1);
      const cardEl = document.createElement("div");
      cardEl.className = "movie-card";
      cardEl.dataset.id = movie.id;

      const depthIndex = visibleCards.length - 1 - index;
      if (depthIndex > 0) {
        const scale = 1 - depthIndex * 0.04;
        const translateY = depthIndex * 12;
        cardEl.style.transform = `scale(${scale}) translateY(${translateY}px)`;
        cardEl.style.zIndex = `${10 - depthIndex}`;
        cardEl.style.opacity = `${1 - depthIndex * 0.15}`;
        cardEl.style.pointerEvents = "none";
      } else {
        cardEl.style.zIndex = "10";
        this.currentCard = cardEl;
        this.currentCard.movieData = movie;
        this.attachListeners(cardEl);
      }

      cardEl.innerHTML = `
        <img class="movie-poster-img" src="${safePosterUrl(movie.poster_path)}" alt="${cardEscape(movie.title)}" loading="lazy" />
        <div class="card-gradient-overlay"></div>
        <div class="stamp stamp-like">LIKE</div>
        <div class="stamp stamp-nope">NOPE</div>
        <div class="card-content">
          <div style="display: flex; align-items: center; gap: 8px;">
            <span class="badge badge--rating">⭐ ${movie.vote_average.toFixed(1)}</span>
            <span class="text-caption" style="color: rgba(255,255,255,0.8);">${cardEscape(movie.release_year || '')}</span>
          </div>
          <h2 class="card-title">${cardEscape(movie.title)}</h2>
          <div class="card-tags-row">
            ${(movie.genres || []).map(g => `<span class="tag">${cardEscape(g)}</span>`).join('')}
            ${(movie.providers || []).map(p => `<span class="tag tag--provider">${cardEscape(p)}</span>`).join('')}
          </div>
        </div>
      `;

      this.container.appendChild(cardEl);
    });
  }

  attachListeners(el) {
    el.addEventListener("pointerdown", this.boundPointerDown);
  }

  detachListeners(el) {
    if (!el) return;
    el.removeEventListener("pointerdown", this.boundPointerDown);
    window.removeEventListener("pointermove", this.boundPointerMove);
    window.removeEventListener("pointerup", this.boundPointerUp);
    window.removeEventListener("pointercancel", this.boundPointerUp);
  }

  handlePointerDown(e) {
    if (!this.currentCard) return;
    this.isDragging = true;
    this.startX = e.clientX;
    this.startY = e.clientY;
    this.currentX = 0;
    this.currentY = 0;

    this.currentCard.style.transition = "none";
    window.addEventListener("pointermove", this.boundPointerMove);
    window.addEventListener("pointerup", this.boundPointerUp);
    window.addEventListener("pointercancel", this.boundPointerUp);
  }

  handlePointerMove(e) {
    if (!this.isDragging || !this.currentCard) return;
    this.currentX = e.clientX - this.startX;
    this.currentY = e.clientY - this.startY;

    const rot = this.currentX * 0.06;
    this.currentCard.style.transform = `translate(${this.currentX}px, ${this.currentY * 0.35}px) rotate(${rot}deg)`;

    const stampLike = this.currentCard.querySelector(".stamp-like");
    const stampNope = this.currentCard.querySelector(".stamp-nope");

    if (this.currentX > 0) {
      if (stampLike) stampLike.style.opacity = Math.min(this.currentX / this.threshold, 1);
      if (stampNope) stampNope.style.opacity = 0;
    } else {
      if (stampNope) stampNope.style.opacity = Math.min(-this.currentX / this.threshold, 1);
      if (stampLike) stampLike.style.opacity = 0;
    }
  }

  handlePointerUp() {
    if (!this.isDragging || !this.currentCard) return;
    this.isDragging = false;
    window.removeEventListener("pointermove", this.boundPointerMove);
    window.removeEventListener("pointerup", this.boundPointerUp);
    window.removeEventListener("pointercancel", this.boundPointerUp);

    if (Math.abs(this.currentX) > this.threshold) {
      const direction = this.currentX > 0 ? "right" : "left";
      this.swipeCard(direction);
    } else {
      this.currentCard.style.transition = "transform 0.3s cubic-bezier(0.16, 1, 0.3, 1)";
      this.currentCard.style.transform = "translate(0px, 0px) rotate(0deg)";
      
      const stampLike = this.currentCard.querySelector(".stamp-like");
      const stampNope = this.currentCard.querySelector(".stamp-nope");
      if (stampLike) stampLike.style.opacity = 0;
      if (stampNope) stampNope.style.opacity = 0;
    }
  }

  swipeCard(direction) {
    if (!this.currentCard) return;
    const cardEl = this.currentCard;
    const movie = cardEl.movieData;
    this.detachListeners(cardEl);

    if (navigator.vibrate) {
      navigator.vibrate(direction === "right" ? [30, 20, 30] : 20);
    }

    const flyX = direction === "right" ? window.innerWidth + 300 : -window.innerWidth - 300;
    const flyRot = direction === "right" ? 30 : -30;
    
    cardEl.style.transition = "transform 0.35s cubic-bezier(0.2, 0.8, 0.4, 1), opacity 0.3s";
    cardEl.style.transform = `translate(${flyX}px, ${this.currentY || 0}px) rotate(${flyRot}deg)`;
    cardEl.style.opacity = "0";

    this.cards.shift();
    this.currentCard = null;

    setTimeout(() => {
      cardEl.remove();
      this.renderStack();
      if (this.onSwipeCallback) {
        this.onSwipeCallback(direction === "right", movie);
      }
    }, 250);
  }

  swipeRight() {
    if (!this.currentCard) return;
    const stampLike = this.currentCard.querySelector(".stamp-like");
    if (stampLike) stampLike.style.opacity = 1;
    this.swipeCard("right");
  }

  swipeLeft() {
    if (!this.currentCard) return;
    const stampNope = this.currentCard.querySelector(".stamp-nope");
    if (stampNope) stampNope.style.opacity = 1;
    this.swipeCard("left");
  }

  getCurrentMovie() {
    return this.currentCard ? this.currentCard.movieData : (this.cards[0] || null);
  }
}
