(() => {
  const mapNode = document.getElementById('map');
  if (!mapNode || typeof L === 'undefined') return;
  const token = mapNode.dataset.token;
  const lat = Number(mapNode.dataset.lat);
  const lon = Number(mapNode.dataset.lon);
  const csrf = mapNode.dataset.csrf;
  const radius = Number(mapNode.dataset.radius || 350);
  const nativeMinZoom = Number(mapNode.dataset.minZoom || 15);
  const nativeMaxZoom = Number(mapNode.dataset.maxNativeZoom || 19);
  const displayMaxZoom = Math.max(24, nativeMaxZoom);
  const latDelta = radius / 111320;
  const lonDelta = radius / (111320 * Math.max(Math.cos(lat * Math.PI / 180), 0.2));
  const imageBounds = L.latLngBounds(
    [lat - latDelta, lon - lonDelta],
    [lat + latDelta, lon + lonDelta],
  );
  const draftKey = `predios-remotos-draft-${token.slice(0, 12)}`;
  const map = L.map(mapNode, {
    zoomControl: true,
    attributionControl: false,
    tap: false,
    maxBounds: imageBounds,
    maxBoundsViscosity: 1,
    zoomSnap: 0.25,
  });
  const containmentMinZoom = map.getBoundsZoom(imageBounds, true);
  map.setMinZoom(Math.max(nativeMinZoom, containmentMinZoom));
  map.setMaxZoom(displayMaxZoom);
  map.setView([lat, lon], Math.max(18, map.getMinZoom()));
  if (mapNode.dataset.window === 'LISTO') {
    L.tileLayer(`/p/${encodeURIComponent(token)}/tiles/{z}/{x}/{y}.png`, {
      minZoom: nativeMinZoom,
      maxZoom: displayMaxZoom,
      maxNativeZoom: nativeMaxZoom,
      noWrap: true,
      bounds: imageBounds,
      keepBuffer: 2,
    }).addTo(map);
  }
  L.circleMarker([lat, lon], { radius: 8, weight: 3, color: '#162f3e', fillColor: '#44bec6', fillOpacity: 1 }).addTo(map).bindTooltip('Punto de referencia');
  L.circle([lat, lon], { radius: 300, color: '#096e74', weight: 1, fill: false, dashArray: '6 6', interactive: false }).addTo(map);
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
  const simplifyLatLngs = points => {
    if (points.length <= 12) return points;
    const projected = points.map(point => map.latLngToContainerPoint(point));
    let tolerance = 5;
    let simplified = L.LineUtil.simplify(projected, tolerance);
    while (simplified.length > 12 && tolerance < 25) {
      tolerance += 2;
      simplified = L.LineUtil.simplify(projected, tolerance);
    }
    if (simplified.length > 12) {
      const step = Math.ceil(simplified.length / 12);
      simplified = simplified.filter((_, index) => index % step === 0 || index === simplified.length - 1);
    }
    return simplified.map(point => map.containerPointToLatLng(point));
  };
  const simplifyGeometry = geometry => {
    if (!geometry || geometry.type !== 'Polygon' || !geometry.coordinates?.[0]) return geometry;
    const ring = geometry.coordinates[0];
    const openRing = ring.length > 1 && ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1]
      ? ring.slice(0, -1)
      : ring.slice();
    if (openRing.length <= 12) return geometry;
    const simplified = simplifyLatLngs(openRing.map(([lng, latitude]) => L.latLng(latitude, lng)));
    if (simplified.length < 3) return geometry;
    const coordinates = simplified.map(point => [point.lng, point.lat]);
    coordinates.push(coordinates[0]);
    return { type: 'Polygon', coordinates: [coordinates] };
  };
  const snapshot = () => {
    if (!activeLayer) return;
    const geometry = activeLayer.toGeoJSON().geometry;
    sessionStorage.setItem(draftKey, JSON.stringify(geometry));
    history.push(JSON.stringify(geometry));
    if (history.length > 20) history.shift();
    confirmButton.disabled = false;
    undoButton.disabled = history.length < 2;
  };
  const setLayer = (geometry, save = true) => {
    const normalizedGeometry = simplifyGeometry(geometry);
    if (activeLayer) map.removeLayer(activeLayer);
    activeLayer = L.geoJSON(normalizedGeometry, { style: { color: '#44bec6', weight: 4, fill: false } }).getLayers()[0];
    activeLayer.addTo(map);
    activeLayer.on('pm:edit', snapshot);
    if (save) snapshot();
    confirmButton.disabled = false;
  };
  const initial = (() => {
    const draft = sessionStorage.getItem(draftKey);
    if (draft) return JSON.parse(draft);
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
    if (name === 'edit' && activeLayer) { activeLayer.pm.enable({ snappable: false, hideMiddleMarkers: true }); notify('Arrastre los puntos grandes para corregir'); }
    if (name === 'delete') {
      if (activeLayer) map.removeLayer(activeLayer);
      activeLayer = null; history = []; sessionStorage.removeItem(draftKey);
      confirmButton.disabled = true; undoButton.disabled = true;
      setTool('move'); notify('Dibujo borrado');
    }
  };
  document.querySelectorAll('.tool').forEach(button => button.addEventListener('click', () => setTool(button.dataset.tool)));
  map.on('pm:create', event => { setLayer(event.layer.toGeoJSON().geometry); map.removeLayer(event.layer); setTool('edit'); });

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
    if (simplified.length > 3 && map.latLngToContainerPoint(simplified[0]).distanceTo(map.latLngToContainerPoint(simplified[simplified.length - 1])) <= freehandClosePixels) simplified.pop();
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
    if (!freehand || !drawing || freePoints.length >= 500) return;
    const point = pointFromEvent(event);
    const previous = freePoints[freePoints.length - 1];
    const pointPixel = map.latLngToContainerPoint(point);
    if (pointPixel.distanceTo(map.latLngToContainerPoint(previous)) < 4) return;
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
      const response = await fetch(`/p/${encodeURIComponent(token)}/submit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ csrf, geometry: activeLayer.toGeoJSON().geometry }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'No fue posible guardar');
      sessionStorage.removeItem(draftKey);
      window.location.assign(result.redirect_url);
    } catch (error) { confirmDialog.close(); notify(`${error.message}. El dibujo sigue aquí para reintentar.`, 'error'); }
    finally { button.disabled = false; button.textContent = 'Sí, enviar'; }
  });
  window.addEventListener('orientationchange', () => window.setTimeout(() => map.invalidateSize(), 250));
})();
