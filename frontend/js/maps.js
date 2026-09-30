// Leaflet and imagery helpers. Satellite imagery: Esri World Imagery
// (no key; attribution required - check Esri's terms before commercial use).
(function (SL) {

  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";

  const ATTRIBUTION = "Imagery &copy; Esri, Maxar, Earthstar Geographics";

  SL.LIME = "#b8f040";

  SL.maps = {

    // New Leaflet map with satellite imagery and (optionally) place labels.
    create(el, options = {}) {
      const map = L.map(el, Object.assign({zoomControl: false, attributionControl: true}, options));
      L.tileLayer(`${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`, {
        maxZoom: 20, maxNativeZoom: 19, attribution: ATTRIBUTION,
      }).addTo(map);
      if (options.labels !== false) {
        L.tileLayer(`${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`, {
          maxZoom: 20, maxNativeZoom: 19, opacity: 0.85,
        }).addTo(map);
      }
      map.attributionControl.setPrefix(false);
      return map;
    },

    // Static, non-interactive background map.
    background(el, lat, lon, zoom) {
      return SL.maps.create(el, {
        dragging: false, scrollWheelZoom: false, doubleClickZoom: false, boxZoom: false,
        keyboard: false, touchZoom: false, labels: false,
      }).setView([lat, lon], zoom);
    },

    parcelStyle(active) {
      return {
        color: SL.LIME, weight: active ? 3 : 2, opacity: 1,
        fillColor: SL.LIME, fillOpacity: active ? 0.22 : 0.12,
      };
    },

    // GeoJSON polygon layer, or null when there is no geometry.
    parcel(geometry, active) {
      return geometry ? L.geoJSON(geometry, {style: SL.maps.parcelStyle(active)}) : null;
    },

    pin(lat, lon, label, top) {
      const size = top ? 34 : 26;
      return L.marker([lat, lon], {
        zIndexOffset: top ? 1000 : 0,
        icon: L.divIcon({
          className: "",
          html: `<div class="map-pin${top ? " top" : ""}">${SL.esc(label)}</div>`,
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        }),
      });
    },

    placeTag(lat, lon, text) {
      return L.marker([lat, lon], {
        interactive: false,
        icon: L.divIcon({className: "", html: `<div class="map-tag">${SL.esc(text)}</div>`, iconSize: null}),
      });
    },

    // Bounds [[s, w], [n, e]] of a candidate (geometry or point).
    bounds(candidate) {
      const pts = [];
      const walk = c => (typeof c[0] === "number" ? pts.push(c) : c.forEach(walk));
      if (candidate.geometry) walk(candidate.geometry.coordinates);
      if (!pts.length) pts.push([candidate.longitude, candidate.latitude]);
      const lons = pts.map(p => p[0]), lats = pts.map(p => p[1]);
      return [[Math.min(...lats), Math.min(...lons)], [Math.max(...lats), Math.max(...lons)]];
    },

    // Static satellite thumbnail with the parcel outline drawn on top.
    // Uses Esri's export endpoint in EPSG:4326, so lon/lat map linearly
    // to pixels; the box is padded to the image aspect ratio.
    thumb(candidate, width = 320, height = 200) {
      let [[s, w], [n, e]] = SL.maps.bounds(candidate);
      const minSpan = 0.0012; // ~130 m
      const cy = (s + n) / 2, cx = (w + e) / 2;
      let spanY = Math.max((n - s) * 1.5, minSpan);
      let spanX = Math.max((e - w) * 1.5, minSpan);
      if (spanX / spanY > width / height) spanY = spanX * height / width;
      else spanX = spanY * width / height;
      s = cy - spanY / 2; n = cy + spanY / 2; w = cx - spanX / 2; e = cx + spanX / 2;

      const src = `${ESRI}/World_Imagery/MapServer/export?bbox=${w},${s},${e},${n}` +
        `&bboxSR=4326&imageSR=4326&size=${width},${height}&format=jpg&f=image`;

      const px = ([lon, lat]) => `${((lon - w) / spanX * width).toFixed(1)},${((n - lat) / spanY * height).toFixed(1)}`;
      let shapes = "";
      const g = candidate.geometry;
      if (g) {
        const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
        shapes = polys.map(poly =>
          `<polygon points="${poly[0].map(px).join(" ")}" />`).join("");
      }
      if (!shapes) {
        const [x, y] = px([candidate.longitude, candidate.latitude]).split(",");
        shapes = `<circle cx="${x}" cy="${y}" r="7" />`;
      }
      return `<div class="thumb"><img src="${src}" alt="" loading="lazy">` +
        `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${shapes}</svg></div>`;
    },

    // Square [[s, w], [n, e]] around a point, radius in km.
    searchBox(lat, lon, radiusKm) {
      const dLat = radiusKm / 111;
      const dLon = radiusKm / (111 * Math.max(Math.abs(Math.cos(lat * Math.PI / 180)), 0.1));
      return [[lat - dLat, lon - dLon], [lat + dLat, lon + dLon]];
    },

    // Static satellite image of an area, for category cards.
    areaImage(lat, lon, spanDeg, width = 360, height = 240) {
      const spanY = spanDeg * height / width;
      return `${ESRI}/World_Imagery/MapServer/export?bbox=${lon - spanDeg / 2},${lat - spanY / 2},` +
        `${lon + spanDeg / 2},${lat + spanY / 2}&bboxSR=4326&imageSR=4326&size=${width},${height}&format=jpg&f=image`;
    },
  };

})(window.SL);
