(() => {
  const abortError = () => new DOMException('Carga reemplazada por otra vista', 'AbortError');
  const wait = (milliseconds, signal) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve(); }, milliseconds);
    const cancel = () => { clearTimeout(timer); reject(abortError()); };
    signal.addEventListener('abort', cancel, { once: true });
  });

  class ImageCache {
    constructor(urlFor, minZoom, limit = 192, concurrency = 4) {
      this.urlFor = urlFor;
      this.minZoom = minZoom;
      this.limit = limit;
      this.concurrency = concurrency;
      this.entries = new Map();
      this.queue = [];
      this.active = 0;
      this.sequence = 0;
    }

    key(coords) { return coords.join('/'); }

    peek(coords) {
      const entry = this.entries.get(this.key(coords));
      if (!entry?.image) return null;
      entry.used = ++this.sequence;
      return entry.image;
    }

    load(coords, priority = 0) {
      const key = this.key(coords);
      const existing = this.entries.get(key);
      if (existing) {
        existing.priority = Math.min(existing.priority, priority);
        existing.used = ++this.sequence;
        return existing.promise;
      }
      const entry = { key, coords, priority, used: ++this.sequence, controller: new AbortController(), image: null, state: 'queued' };
      entry.promise = new Promise((resolve, reject) => { entry.resolve = resolve; entry.reject = reject; });
      // Cancellation may happen after a tile has left the view.
      entry.promise.catch(() => {});
      this.entries.set(key, entry);
      this.queue.push(entry);
      this.pump();
      return entry.promise;
    }

    pump() {
      this.queue.sort((a, b) => a.priority - b.priority || a.used - b.used);
      while (this.active < this.concurrency && this.queue.length) {
        const entry = this.queue.shift();
        if (entry.controller.signal.aborted) continue;
        entry.state = 'running';
        this.active += 1;
        this.read(entry).then(image => {
          if (entry.controller.signal.aborted) { image.close?.(); throw abortError(); }
          entry.image = image;
          entry.state = 'ready';
          entry.used = ++this.sequence;
          entry.resolve(image);
          this.trim();
        }).catch(error => {
          if (this.entries.get(entry.key) === entry) this.entries.delete(entry.key);
          entry.reject(error);
        }).finally(() => { this.active -= 1; this.pump(); });
      }
    }

    async read(entry) {
      const signal = entry.controller.signal;
      let lastError;
      for (let attempt = 0; attempt < 4; attempt += 1) {
        if (signal.aborted) throw abortError();
        const request = new AbortController();
        const cancel = () => request.abort();
        signal.addEventListener('abort', cancel, { once: true });
        const timeout = setTimeout(cancel, 15000);
        let retryAfter = 0;
        let permanent = false;
        try {
          const response = await fetch(this.urlFor(entry.coords), { signal: request.signal, credentials: 'same-origin' });
          if (!response.ok) {
            permanent = ![408, 429, 500, 502, 503, 504].includes(response.status);
            retryAfter = Math.min(8000, Math.max(0, Number(response.headers.get('Retry-After')) * 1000 || 0));
            throw new Error(`No se pudo cargar una zona (${response.status}).`);
          }
          const blob = await response.blob();
          if (typeof createImageBitmap === 'function') return await createImageBitmap(blob);
          // Older browsers decode the same URL from their HTTP cache.
          return await new Promise((resolve, reject) => {
            const image = new Image();
            const aborted = () => { image.src = ''; reject(new Error('Se agotó el tiempo para cargar la imagen.')); };
            request.signal.addEventListener('abort', aborted, { once: true });
            image.onload = () => { request.signal.removeEventListener('abort', aborted); resolve(image); };
            image.onerror = () => { request.signal.removeEventListener('abort', aborted); reject(new Error('La imagen recibida no es válida.')); };
            if (request.signal.aborted) { aborted(); return; }
            image.src = this.urlFor(entry.coords);
          });
        } catch (error) {
          if (signal.aborted) throw abortError();
          lastError = error;
          if (permanent || attempt === 3) throw error;
        } finally {
          clearTimeout(timeout);
          signal.removeEventListener('abort', cancel);
        }
        await wait(retryAfter || Math.min(4000, 300 * 2 ** attempt), signal);
      }
      throw lastError;
    }

    trim() {
      const ready = [...this.entries.values()].filter(entry => entry.image);
      ready.sort((a, b) => a.used - b.used);
      while (ready.length > this.limit) {
        const index = ready.findIndex(entry => entry.coords[0] > this.minZoom + 1);
        if (index < 0) break;
        const [entry] = ready.splice(index, 1);
        this.entries.delete(entry.key);
        entry.image.close?.();
      }
    }

    cancelPrefetch() {
      for (const entry of this.entries.values()) {
        if (entry.priority !== 1 || entry.image) continue;
        entry.controller.abort();
        if (entry.state === 'queued') entry.reject(abortError());
        this.entries.delete(entry.key);
      }
      this.queue = this.queue.filter(entry => !entry.controller.signal.aborted);
    }

    selectView(coords) {
      const visible = new Set(coords.map(item => this.key(item)));
      // Preserve overlapping requests during a short pan. Withdraw only detail
      // that has left the viewport, allowing the new view to take priority.
      for (const entry of this.entries.values()) {
        if (entry.image) continue;
        entry.priority = visible.has(entry.key) ? 0 : 1;
      }
      this.cancelPrefetch();
    }
  }

  const CanvasTiles = L.GridLayer.extend({
    initialize(cache, options) {
      this.cache = cache;
      this.states = new WeakMap();
      this.visible = new Set();
      L.setOptions(this, options);
      this.on('tileunload', event => { event.tile.dataset.discarded = 'true'; });
    },

    createTile(coords, done) {
      const tile = document.createElement('canvas');
      tile.width = tile.height = 256;
      const coordinates = [coords.z, coords.x, coords.y];
      this.states.set(tile, { coordinates, key: coordinates.join('/'), done, completed: false, pending: false });
      tile.dataset.imageState = 'loading';
      this.paintCached(tile, coordinates);
      if (this._map.dragging.moving()) {
        // Show cached coverage while dragging. Request new detail only for the
        // resting viewport, avoiding raster work for briefly crossed areas.
        L.Util.requestAnimFrame(() => { if (!tile.dataset.discarded) this.finishTile(tile, null); });
      } else {
        this.requestTile(tile);
      }
      return tile;
    },

    paintCached(tile, [z, x, y]) {
      const context = tile.getContext('2d');
      const exact = this.cache.peek([z, x, y]);
      if (exact) {
        context.clearRect(0, 0, 256, 256);
        context.drawImage(exact, 0, 0, 256, 256);
        tile.dataset.imageState = 'ready';
        tile.style.visibility = 'visible';
      } else {
        // Paint authorized parent imagery immediately, keeping the map visible
        // while the sharper tile is fetched and decoded.
        for (let zoom = z - 1; zoom >= this.options.minZoom; zoom -= 1) {
          const scale = 2 ** (z - zoom);
          const parent = this.cache.peek([zoom, Math.floor(x / scale), Math.floor(y / scale)]);
          if (!parent) continue;
          const width = parent.width || parent.naturalWidth;
          const height = parent.height || parent.naturalHeight;
          const sx = (x % scale) * width / scale;
          const sy = (y % scale) * height / scale;
          context.clearRect(0, 0, 256, 256);
          context.drawImage(parent, sx, sy, width / scale, height / scale, 0, 0, 256, 256);
          tile.dataset.imageState = 'fallback';
          tile.style.visibility = 'visible';
          break;
        }
      }
    },

    requestTile(tile) {
      const state = this.states.get(tile);
      if (state.pending || tile.dataset.discarded) return;
      state.pending = true;
      this.cache.load(state.coordinates, 0).then(image => {
        if (tile.dataset.discarded) return;
        const context = tile.getContext('2d');
        context.clearRect(0, 0, 256, 256);
        context.drawImage(image, 0, 0, 256, 256);
        tile.dataset.imageState = 'ready';
        tile.style.visibility = 'visible';
        this.finishTile(tile, null);
      }).catch(error => {
        if (tile.dataset.discarded) return;
        if (error.name === 'AbortError') {
          // Leaflet must finish its old request even when the browser withdraws
          // it. A retained canvas can request detail again when it returns.
          this.finishTile(tile, null);
          return;
        }
        if (tile.dataset.imageState !== 'fallback') tile.dataset.imageState = 'error';
        this.finishTile(tile, error);
      }).finally(() => {
        state.pending = false;
        if (tile.dataset.discarded) return;
        // A quick return can precede the cancelled request's rejection.
        if (this.visible.has(state.key) && tile.dataset.imageState !== 'ready') {
          // Only cancellation should immediately restart; permanent failures
          // retain the explicit retry action instead of looping indefinitely.
          if (state.cancelled) this.requestTile(tile);
        }
        if (this.visible.size && state.completed && !this.visiblePending()) this.fire('load');
      });
    },

    finishTile(tile, error) {
      const state = this.states.get(tile);
      state.cancelled = !error && tile.dataset.imageState !== 'ready';
      if (!state.completed) {
        state.completed = true;
        state.done(error, tile);
      } else if (error) {
        this.fire('tileerror', { error, tile, coords: L.point(state.coordinates[1], state.coordinates[2]) });
      }
    },

    visiblePending() {
      return Object.values(this._tiles).some(record => {
        const state = this.states.get(record.el);
        return state && this.visible.has(state.key) && state.pending;
      });
    },

    refreshVisible(coordinates) {
      this.visible = new Set(coordinates.map(coords => coords.join('/')));
      const retained = Object.values(this._tiles).filter(record => {
        const state = this.states.get(record.el);
        return state && this.visible.has(state.key) && !state.pending && record.el.dataset.imageState !== 'ready';
      });
      if (retained.length) this.fire('loading');
      for (const record of retained) {
        const state = this.states.get(record.el);
        this.paintCached(record.el, state.coordinates);
        this.requestTile(record.el);
      }
    },

  });

  window.PrediosImagery = { ImageCache, CanvasTiles };
})();
