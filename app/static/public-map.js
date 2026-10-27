(() => {
  const mapNode = document.getElementById('map');
  if (!mapNode || typeof L === 'undefined') return;
  const token = mapNode.dataset.token;
  const lat = Number(mapNode.dataset.lat);
  const lon = Number(mapNode.dataset.lon);
  const csrf = mapNode.dataset.csrf;
  const radius = Number(mapNode.dataset.radius || 2000);
  const nativeMinZoom = Number(mapNode.dataset.minZoom || 15);
  const nativeMaxZoom = Number(mapNode.dataset.maxNativeZoom || 19);
  const imageVersion = mapNode.dataset.imageVersion || '';
  const displayMaxZoom = nativeMaxZoom;
  const latDelta = radius / 111320;
  const lonDelta = radius / (111320 * Math.max(Math.cos(lat * Math.PI / 180), 0.2));
  const imageBounds = mapNode.dataset.bounds ? L.latLngBounds(JSON.parse(mapNode.dataset.bounds)) : L.latLngBounds(
    [lat - latDelta, lon - lonDelta],
    [lat + latDelta, lon + lonDelta],
  );
  const fullImagery = mapNode.dataset.full === 'true';
  const allowedRadius = radius;
  const allowedBounds = imageBounds;
  const draftKey = `predios-remotos-draft-${token.slice(0, 12)}`;
  const map = L.map(mapNode, {
    zoomControl: true,
    attributionControl: false,
    tap: false,
    maxBounds: imageBounds,
    maxBoundsViscosity: 1,
    zoomSnap: 1,
    zoomAnimation: true,
    fadeAnimation: false,
  });
  const overviewMinZoom = Math.floor(map.getBoundsZoom(allowedBounds, false));
  map.setMinZoom(Math.max(nativeMinZoom, overviewMinZoom));
  map.setMaxZoom(displayMaxZoom);
  const goToCenter = () => map.setView([lat, lon], Math.max(map.getMinZoom(), Math.min(16, nativeMaxZoom)));
  if (fullImagery) goToCenter();
  else map.fitBounds(allowedBounds, { padding: [20, 20], maxZoom: Math.min(16, nativeMaxZoom) });
  document.getElementById('centerMap')?.addEventListener('click', goToCenter);
  document.getElementById('showAllImagery')?.addEventListener('click', () => map.fitBounds(imageBounds, { padding: [20, 20] }));
  mapNode.dataset.minDisplayZoom = String(map.getMinZoom());
  const urlFor = ([z, x, y]) => `/p/${encodeURIComponent(token)}/tiles/${z}/${x}/${y}.png?v=${encodeURIComponent(imageVersion)}`;
  const imageCache = new PrediosImagery.ImageCache(urlFor, nativeMinZoom);
  let imageLayer = null;
  let failedTiles = 0;
  let mapReady = false;
  if (mapNode.dataset.window === 'LISTO') {
    imageLayer = new PrediosImagery.CanvasTiles(imageCache, {
      minZoom: nativeMinZoom,
      maxZoom: displayMaxZoom,
      maxNativeZoom: nativeMaxZoom,
      noWrap: true,
      bounds: imageBounds,
      keepBuffer: 3,
      updateWhenZooming: false,
      updateWhenIdle: false,
      updateInterval: 120,
    });
    const status = document.getElementById('mapStatus');
    const statusText = document.getElementById('mapStatusText');
    const retry = document.getElementById('retryMap');
    const readyText = statusText.textContent;
    imageLayer.on('loading', () => {
      failedTiles = 0;
      statusText.textContent = 'Cargando ortofoto original…';
      retry.hidden = true;
    });
    imageLayer.on('tileerror', () => {
      failedTiles += 1;
      statusText.textContent = 'No se pudieron cargar algunas zonas.';
      retry.hidden = false;
    });
    imageLayer.on('load', () => {
      if (!failedTiles) {
        statusText.textContent = readyText;
        retry.hidden = true;
      }
      status.dataset.errors = String(failedTiles);
    });
    retry.addEventListener('click', () => imageLayer.redraw());
  }
  L.circleMarker([lat, lon], { radius: 8, weight: 3, color: '#162f3e', fillColor: '#44bec6', fillOpacity: 1 }).addTo(map).bindTooltip('Punto de referencia');
  if (!fullImagery) L.circle([lat, lon], { radius: allowedRadius, color: '#096e74', weight: 1, fill: false, dashArray: '6 6', interactive: false }).addTo(map);
  const syncMapState = () => {
    const center = map.getCenter();
    mapNode.dataset.zoom = String(map.getZoom());
    mapNode.dataset.centerLat = center.lat.toFixed(7);
    mapNode.dataset.centerLon = center.lng.toFixed(7);
  };
  map.on('zoomend moveend', syncMapState);
  syncMapState();

  let activeLayer = null;
  let history = [];
  let freehand = false;
  let drawing = false;
  let freePoints = [];
  let freehandHalo = null;
  let freehandLine = null;
  let freehandStart = null;
  let freehandCursor = null;
  let leftStart = false;
  const freehandClosePixels = 22;
  const freehandLeavePixels = 40;
  const confirmButton = document.getElementById('confirmButton');
  const undoButton = document.getElementById('undoButton');
  const toast = document.getElementById('toast');

  const notify = (message, kind = '') => {
    toast.textContent = message;
    toast.className = `toast show ${kind}`;
    window.setTimeout(() => toast.className = 'toast', 3500);
  };
  // Only a completed freehand trace is simplified. Manual vertices, restored
  // drafts, edits and undo snapshots must retain their exact coordinates.
  const simplifyLatLngs = points => {
    const projected = points.map(point => map.latLngToContainerPoint(point));
    const limit = 40;
    let simplified = L.LineUtil.simplify(projected, 1.25);
    if (simplified.length > limit) {
      let low = 1.25;
      let high = 2;
      while (L.LineUtil.simplify(projected, high).length > limit) high *= 2;
      for (let iteration = 0; iteration < 16; iteration += 1) {
        const tolerance = (low + high) / 2;
        if (L.LineUtil.simplify(projected, tolerance).length > limit) low = tolerance;
        else high = tolerance;
      }
      simplified = L.LineUtil.simplify(projected, high);
    }
    return simplified.map(point => map.containerPointToLatLng(point));
  };
  const snapshot = () => {
    if (!activeLayer) return;
    const geometry = activeLayer.toGeoJSON(false).geometry;
    sessionStorage.setItem(draftKey, JSON.stringify(geometry));
    history.push(JSON.stringify(geometry));
    if (history.length > 20) history.shift();
    confirmButton.disabled = !mapReady;
    undoButton.disabled = history.length < 2;
  };
  const setLayer = (geometry, save = true) => {
    if (activeLayer) map.removeLayer(activeLayer);
    activeLayer = L.geoJSON(geometry, { style: { color: '#44bec6', weight: 4, fill: false } }).getLayers()[0];
    activeLayer.addTo(map);
    activeLayer.on('pm:edit', snapshot);
    if (save) snapshot();
    confirmButton.disabled = !mapReady;
  };
  const initial = (() => {
    const draft = sessionStorage.getItem(draftKey);
    if (draft) { try { return JSON.parse(draft); } catch (_) { sessionStorage.removeItem(draftKey); } }
    try { return JSON.parse(mapNode.dataset.current); } catch (_) { return null; }
  })();
  if (initial) setLayer(initial);

  const setTool = (name) => {
    document.querySelectorAll('.tool').forEach(button => button.classList.toggle('active', button.dataset.tool === name));
    map.pm.disableDraw();
    if (activeLayer) activeLayer.pm.disable();
    freehand = false;
    map.dragging.enable();
    if (name === 'polygon') map.pm.enableDraw('Polygon', { snappable: false, finishOn: 'dblclick', pathOptions: { color: '#44bec6', fill: false, weight: 4 } });
    if (name === 'freehand') { freehand = true; map.dragging.disable(); notify('Siga el borde y vuelva al punto inicial para cerrar'); }
    if (name === 'edit' && activeLayer) {
      activeLayer.pm.enable({ snappable: false, hideMiddleMarkers: false });
      notify('Mueva los puntos grandes o use los pequeños para afinar el contorno');
    }
    if (name === 'delete') {
      if (activeLayer) map.removeLayer(activeLayer);
      activeLayer = null; history = []; sessionStorage.removeItem(draftKey);
      confirmButton.disabled = true; undoButton.disabled = true;
      setTool('move'); notify('Dibujo borrado');
    }
  };
  document.querySelectorAll('.tool').forEach(button => button.addEventListener('click', () => setTool(button.dataset.tool)));
  map.on('pm:create', event => { setLayer(event.layer.toGeoJSON(false).geometry); map.removeLayer(event.layer); setTool('edit'); });

  const pointFromEvent = event => {
    const rect = mapNode.getBoundingClientRect();
    return map.containerPointToLatLng(L.point(event.clientX - rect.left, event.clientY - rect.top));
  };
  const clearFreehandGuide = () => {
    if (freehandHalo) map.removeLayer(freehandHalo);
    if (freehandLine) map.removeLayer(freehandLine);
    if (freehandStart) map.removeLayer(freehandStart);
    if (freehandCursor) map.removeLayer(freehandCursor);
    freehandHalo = null;
    freehandLine = null;
    freehandStart = null;
    freehandCursor = null;
  };
  const simplifyFreehand = points => {
    const simplified = simplifyLatLngs(points);
    if (simplified.length > 3 && map.latLngToContainerPoint(simplified[0]).distanceTo(map.latLngToContainerPoint(simplified[simplified.length - 1])) <= 2) simplified.pop();
    return simplified;
  };
  const finishFreehand = (event, closedAtStart = false) => {
    if (!drawing) return;
    drawing = false;
    if (mapNode.hasPointerCapture(event.pointerId)) mapNode.releasePointerCapture(event.pointerId);
    const simplified = simplifyFreehand(freePoints);
    clearFreehandGuide();
    if (simplified.length < 3) {
      freePoints = [];
      notify('Trace un contorno más largo', 'error');
      return;
    }
    const coordinates = simplified.map(point => [point.lng, point.lat]);
    coordinates.push(coordinates[0]);
    setLayer({ type: 'Polygon', coordinates: [coordinates] });
    setTool('move');
    const closeMessage = closedAtStart ? 'Contorno cerrado' : 'Contorno terminado';
    notify(`${closeMessage}: ${simplified.length} puntos. Pulse Editar para corregir.`);
    freePoints = [];
  };
  mapNode.addEventListener('pointerdown', event => {
    if (!freehand) return;
    event.preventDefault();
    clearFreehandGuide();
    drawing = true;
    leftStart = false;
    freePoints = [pointFromEvent(event)];
    freehandHalo = L.polyline(freePoints, { color: '#fff', weight: 11, opacity: 0.95, className: 'freehand-guide-halo', interactive: false }).addTo(map);
    freehandLine = L.polyline(freePoints, { color: '#096e74', weight: 6, opacity: 1, className: 'freehand-guide', interactive: false }).addTo(map);
    freehandStart = L.circleMarker(freePoints[0], { radius: 10, color: '#162f3e', weight: 4, fillColor: '#44bec6', fillOpacity: 1, className: 'freehand-start', interactive: false }).addTo(map);
    freehandCursor = L.circleMarker(freePoints[0], { radius: 7, color: '#fff', weight: 3, fillColor: '#096e74', fillOpacity: 1, className: 'freehand-cursor', interactive: false }).addTo(map);
    mapNode.setPointerCapture(event.pointerId);
  });
  mapNode.addEventListener('pointermove', event => {
    if (!freehand || !drawing || freePoints.length >= 2000) return;
    const point = pointFromEvent(event);
    const previous = freePoints[freePoints.length - 1];
    const pointPixel = map.latLngToContainerPoint(point);
    if (pointPixel.distanceTo(map.latLngToContainerPoint(previous)) < 2) return;
    freePoints.push(point);
    freehandHalo.setLatLngs(freePoints);
    freehandLine.setLatLngs(freePoints);
    freehandCursor.setLatLng(point);
    const startDistance = pointPixel.distanceTo(map.latLngToContainerPoint(freePoints[0]));
    if (!leftStart && startDistance >= freehandLeavePixels) {
      leftStart = true;
      freehandStart.setStyle({ fillColor: '#fff' });
    }
    if (leftStart && freePoints.length >= 6 && startDistance <= freehandClosePixels) finishFreehand(event, true);
  });
  mapNode.addEventListener('pointerup', event => { if (freehand && drawing) finishFreehand(event); });
  mapNode.addEventListener('pointercancel', event => { if (drawing) finishFreehand(event); });

  undoButton.addEventListener('click', () => {
    if (history.length < 2) return;
    history.pop(); const previous = JSON.parse(history[history.length - 1]); setLayer(previous, false);
    sessionStorage.setItem(draftKey, JSON.stringify(previous)); undoButton.disabled = history.length < 2;
  });
  const helpDialog = document.getElementById('helpDialog');
  const confirmDialog = document.getElementById('confirmDialog');
  document.getElementById('helpButton').addEventListener('click', () => helpDialog.showModal());
  confirmButton.addEventListener('click', () => activeLayer && confirmDialog.showModal());
  document.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  document.getElementById('sendButton').addEventListener('click', async event => {
    if (!activeLayer) return;
    const button = event.currentTarget; button.disabled = true; button.textContent = 'Enviando…';
    try {
      const response = await fetch(`/p/${encodeURIComponent(token)}/submit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ csrf, geometry: activeLayer.toGeoJSON(false).geometry }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'No fue posible guardar');
      sessionStorage.removeItem(draftKey);
      window.location.assign(result.redirect_url);
    } catch (error) { confirmDialog.close(); notify(`${error.message}. El dibujo sigue aquí para reintentar.`, 'error'); }
    finally { button.disabled = false; button.textContent = 'Sí, enviar'; }
  });
  const loader = document.getElementById('imageLoader');
  const loadMessage = document.getElementById('imageLoadMessage');
  const retryInitial = document.getElementById('retryInitial');
  const loadImage = (coordinates, priority = 1) => imageCache.load(coordinates, priority);
  const preloadBatch = async (tiles, progress, isCurrent = () => true) => {
    let next = 0;
    let completed = 0;
    const results = await Promise.allSettled(Array.from({ length: Math.min(6, tiles.length) }, async () => {
      while (next < tiles.length) {
        if (!isCurrent()) return;
        const coordinates = tiles[next++];
        await loadImage(coordinates);
        completed += 1;
        if (progress) progress(completed, tiles.length);
      }
    }));
    const failure = results.find(result => result.status === 'rejected');
    if (failure) throw failure.reason;
  };
  const viewportTiles = zoom => {
    const center = map.project(map.getCenter(), zoom);
    const halfSize = map.getSize().divideBy(2);
    const min = center.subtract(halfSize).divideBy(256).floor();
    const max = center.add(halfSize).subtract([1, 1]).divideBy(256).floor();
    const tiles = [];
    for (let x = min.x; x <= max.x; x += 1) {
      for (let y = min.y; y <= max.y; y += 1) {
        const bounds = L.latLngBounds(map.unproject([x * 256, (y + 1) * 256], zoom), map.unproject([(x + 1) * 256, y * 256], zoom));
        if (imageBounds.overlaps(bounds)) tiles.push([zoom, x, y]);
      }
    }
    return tiles;
  };
  const interactions = ['dragging', 'touchZoom', 'scrollWheelZoom', 'doubleClickZoom', 'boxZoom', 'keyboard'];
  const setReady = ready => {
    mapReady = ready;
    mapNode.dataset.imageReady = String(ready);
    mapNode.setAttribute('aria-busy', String(!ready));
    document.querySelectorAll('.tool').forEach(button => { button.disabled = !ready; });
    interactions.forEach(name => { if (ready) map[name].enable(); else map[name].disable(); });
    confirmButton.disabled = !ready || !activeLayer;
  };
  let initializing = false;
  const getManifest = async () => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`/p/${encodeURIComponent(token)}/preload`, { credentials: 'same-origin', signal: controller.signal });
      if (!response.ok && response.status !== 404) throw new Error('La imagen todavía no está disponible.');
      // The previous server can still be serving during an application restart.
      return response.status === 404
        ? { tiles: Array.from({ length: nativeMaxZoom - nativeMinZoom + 1 }, (_, index) => viewportTiles(nativeMinZoom + index)).flat() }
        : await response.json();
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('El servidor está tardando demasiado en responder.');
      throw error;
    } finally { clearTimeout(timer); }
  };
  const prepareImages = async () => {
    if (initializing) return;
    initializing = true;
    retryInitial.hidden = true;
    setReady(false);
    try {
      const manifest = await getManifest();
      const tiles = [...new Map([...viewportTiles(Math.round(map.getZoom())), ...manifest.tiles].map(tile => [tile.join('/'), tile])).values()];
      await preloadBatch(tiles, (done, total) => { loadMessage.textContent = `Cargando imagen y niveles de zoom… ${Math.round(done * 100 / total)}%`; });
      await new Promise((resolve, reject) => {
        imageLayer.once('load', () => failedTiles ? reject(new Error('No se pudo completar la imagen.')) : resolve());
        if (map.hasLayer(imageLayer)) imageLayer.redraw();
        else imageLayer.addTo(map);
      });
      loader.hidden = true;
      setReady(true);
    } catch (error) {
      loadMessage.textContent = `${error.message} Pulse Reintentar.`;
      retryInitial.hidden = false;
    } finally { initializing = false; }
  };
  retryInitial.addEventListener('click', prepareImages);
  setReady(false);
  prepareImages();
  let prefetchTimer;
  let prefetchVersion = 0;
  map.on('movestart zoomstart', () => {
    prefetchVersion += 1;
    if (mapReady) imageCache.cancelPrefetch();
    clearTimeout(prefetchTimer);
  });
  map.on('moveend zoomend', () => {
    if (!mapReady) return;
    clearTimeout(prefetchTimer);
    const version = ++prefetchVersion;
    const visible = viewportTiles(Math.round(map.getZoom()));
    imageCache.selectView(visible);
    imageLayer.refreshVisible(visible);
    prefetchTimer = window.setTimeout(() => {
      const zoom = Math.round(map.getZoom());
      const levels = [zoom + 1, zoom - 1].filter(level => level >= nativeMinZoom && level <= nativeMaxZoom);
      preloadBatch(levels.flatMap(viewportTiles), null, () => version === prefetchVersion).catch(() => {});
    }, 250);
  });
  window.addEventListener('orientationchange', () => window.setTimeout(() => map.invalidateSize(), 250));
})();
