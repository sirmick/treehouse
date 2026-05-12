<script lang="ts">
	import { onMount } from 'svelte';
	import maplibregl, { type StyleSpecification } from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import { Protocol } from 'pmtiles';
	import { layers, namedFlavor } from '@protomaps/basemaps';
	import { activeHost, hostnames } from '$lib/config';

	let container: HTMLDivElement;
	let tilesMissing = $state(false);

	// Map category id (from ingest_kiwix.py's classify()) to UI metadata.
	// `minzoom` keeps low-priority categories hidden at world view so the
	// 100-pin viewport budget goes to the big-deal features; toggling
	// chips in the UI further filters what's drawn.
	type Category = {
		id: string;
		emoji: string;
		label: string;
		color: string;
		minzoom: number;
	};
	const CATEGORIES: Category[] = [
		{ id: 'settlement', emoji: '🏙️', label: 'Places',     color: '#0ea5e9', minzoom: 0 },
		{ id: 'country',    emoji: '🗺️',  label: 'Countries',  color: '#1d4ed8', minzoom: 0 },
		{ id: 'landform',   emoji: '⛰️',  label: 'Landforms',  color: '#65a30d', minzoom: 2 },
		{ id: 'water',      emoji: '🌊',  label: 'Water',      color: '#0891b2', minzoom: 2 },
		{ id: 'structure',  emoji: '🏛️',  label: 'Landmarks',  color: '#f59e0b', minzoom: 4 },
		{ id: 'event',      emoji: '⚔️',  label: 'Events',     color: '#dc2626', minzoom: 4 },
		{ id: 'person',     emoji: '👤',  label: 'People',     color: '#a855f7', minzoom: 5 }
	];
	const CAT_BY_ID: Record<string, Category> = Object.fromEntries(
		CATEGORIES.map((c) => [c.id, c])
	);

	let enabled = $state(new Set(CATEGORIES.map((c) => c.id)));
	function toggle(id: string): void {
		if (enabled.has(id)) enabled.delete(id);
		else enabled.add(id);
		// Trigger reactive update — Svelte 5 $state Set tracks methods.
		enabled = new Set(enabled);
		refreshFn?.();
	}
	let refreshFn: (() => void) | null = null;

	// Path to the PMTiles archive served by host nginx (see the
	// /tiles/ location in ansible/roles/proxy/templates/treehouse.conf.j2).
	// One-shot bootstrap: `make tiles` extracts a planet pmtiles to
	// /srv/treehouse/content/maps/planet.pmtiles on the VM.
	const TILES_URL = '/tiles/planet.pmtiles';

	// Glyphs + sprites currently hotlinked from Protomaps' public CDN
	// (works in lan mode). Self-hosting these for full isolated-mode
	// support is a follow-up; the asset bundle is small (~MB).
	const GLYPHS = 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf';
	const SPRITE = 'https://protomaps.github.io/basemaps-assets/sprites/v4/light';

	// Source id → kiwix vhost id in `hostnames`. Mirrors the table in
	// /search; lifted out so pin clicks navigate to the right FQDN.
	const SOURCE_HOST: Record<string, keyof typeof hostnames> = {
		wikipedia: 'wikipedia',
		wiktionary: 'dictionary',
		vikidia: 'vikidia'
	};

	type GeoHit = {
		title: string;
		source: string;
		deeplink_book: string;
		deeplink_path: string;
		category: string;
		_geo: { lat: number; lng: number };
	};

	function articleUrl(hit: GeoHit): string {
		const host = activeHost(SOURCE_HOST[hit.source] ?? 'wikipedia');
		return `https://${host}/content/${hit.deeplink_book}/${hit.deeplink_path}`;
	}

	onMount(() => {
		const protocol = new Protocol();
		maplibregl.addProtocol('pmtiles', protocol.tile);

		const style: StyleSpecification = {
			version: 8,
			glyphs: GLYPHS,
			sprite: SPRITE,
			sources: {
				protomaps: {
					type: 'vector',
					url: `pmtiles://${TILES_URL}`,
					attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a>'
				}
			},
			layers: layers('protomaps', namedFlavor('light'), { lang: 'en' })
		};

		const map = new maplibregl.Map({
			container,
			style,
			center: [0, 25],
			zoom: 1.2,
			minZoom: 0.8,
			maxZoom: 10
		});

		map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'top-right');
		map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

		// If the pmtiles file isn't on disk yet, MapLibre fires a 404
		// on the first range request — surface a friendlier message.
		map.on('error', (e) => {
			const status = (e.error as { status?: number } | undefined)?.status;
			if (status === 404) tilesMissing = true;
		});

		// --- Article pins layer ----------------------------------------
		// Each geocoded article in MeiliSearch (those with a `_geo` field —
		// chunk 0 only, per ingest_kiwix.py) becomes a pin within the
		// current viewport. We requery on every pan/zoom; 100 hits is
		// plenty at any zoom (more would clutter without filtering by
		// importance, which we don't have yet).
		map.on('load', () => {
			map.addSource('pins', {
				type: 'geojson',
				data: { type: 'FeatureCollection', features: [] }
			});
			// Single circle layer, color from feature.category via match.
			// Cheaper than 7 layers and lets us keep zoom-tiering / chip
			// filtering on the JS side (we just exclude features from
			// the GeoJSON we ship to the source).
			const colorExpr: maplibregl.ExpressionSpecification = [
				'match',
				['get', 'category'],
				...CATEGORIES.flatMap((c) => [c.id, c.color]),
				/* default */ '#94a3b8'
			];
			map.addLayer({
				id: 'pin-circles',
				type: 'circle',
				source: 'pins',
				paint: {
					'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 3, 8, 7],
					'circle-color': colorExpr,
					'circle-stroke-width': 1.5,
					'circle-stroke-color': '#ffffff',
					'circle-opacity': 0.92
				}
			});
			map.addLayer({
				id: 'pin-labels',
				type: 'symbol',
				source: 'pins',
				minzoom: 5,
				layout: {
					'text-field': ['get', 'title'],
					'text-size': 11,
					'text-offset': [0, 0.9],
					'text-anchor': 'top',
					'text-optional': true
				},
				paint: {
					'text-color': '#0f172a',
					'text-halo-color': '#ffffff',
					'text-halo-width': 1.5
				}
			});

			map.on('click', 'pin-circles', (e) => {
				const f = e.features?.[0];
				const url = f?.properties && (f.properties as { url?: string }).url;
				if (url) window.location.href = url;
			});
			map.on('mouseenter', 'pin-circles', () => {
				map.getCanvas().style.cursor = 'pointer';
			});
			map.on('mouseleave', 'pin-circles', () => {
				map.getCanvas().style.cursor = '';
			});

			// Debounce: a single drag fires many move events. We want one
			// query at the end of the gesture.
			let pinTimer: number | undefined;
			let pinAbort: AbortController | undefined;
			const refreshPins = async () => {
				pinAbort?.abort();
				pinAbort = new AbortController();
				const z = map.getZoom();
				// Intersect user-enabled categories with those visible at
				// this zoom level. Empty set → skip the network call.
				const drawable = [...enabled].filter(
					(id) => CAT_BY_ID[id] && CAT_BY_ID[id].minzoom <= z
				);
				if (drawable.length === 0) {
					(map.getSource('pins') as maplibregl.GeoJSONSource).setData({
						type: 'FeatureCollection',
						features: []
					});
					return;
				}
				const b = map.getBounds();
				const ne = b.getNorthEast();
				const sw = b.getSouthWest();
				// Meili: _geoBoundingBox([top_right_lat, lng], [bottom_left_lat, lng])
				// Quote category values so commas inside (none today) wouldn't
				// trip up Meili's filter parser if a category id ever changes.
				const catList = drawable.map((c) => `"${c}"`).join(', ');
				const filter =
					`_geoBoundingBox([${ne.lat}, ${ne.lng}], [${sw.lat}, ${sw.lng}])` +
					` AND category IN [${catList}]`;
				try {
					const r = await fetch('/api/search', {
						method: 'POST',
						headers: { 'Content-Type': 'application/json' },
						body: JSON.stringify({
							q: '',
							filter,
							limit: 100,
							// "Big-deal first" — show capitals before hamlets.
							sort: ['prominence:desc']
						}),
						signal: pinAbort.signal
					});
					if (!r.ok) return;
					const data = (await r.json()) as { hits: GeoHit[] };
					const features = (data.hits ?? [])
						.filter((h) => h._geo)
						.map((h) => ({
							type: 'Feature' as const,
							geometry: {
								type: 'Point' as const,
								coordinates: [h._geo.lng, h._geo.lat]
							},
							properties: {
								title: h.title,
								category: h.category,
								url: articleUrl(h)
							}
						}));
					(map.getSource('pins') as maplibregl.GeoJSONSource).setData({
						type: 'FeatureCollection',
						features
					});
				} catch (err) {
					if (err instanceof DOMException && err.name === 'AbortError') return;
					// Network / API errors aren't fatal — pins just won't update.
				}
			};
			refreshFn = () => {
				if (pinTimer) clearTimeout(pinTimer);
				pinTimer = window.setTimeout(refreshPins, 100);
			};
			const onMove = () => {
				if (pinTimer) clearTimeout(pinTimer);
				pinTimer = window.setTimeout(refreshPins, 300);
			};
			map.on('moveend', onMove);
			refreshPins();
		});

		return () => {
			map.remove();
			maplibregl.removeProtocol('pmtiles');
		};
	});
</script>

<svelte:head>
	<title>Maps · Treehouse</title>
</svelte:head>

<main class="flex min-h-screen flex-col px-4 py-4 sm:px-8 sm:py-6">
	<!--
		Filter chips: toggle which article categories show on the map.
		Disabled chips get a strikethrough vibe; each chip wears its
		category colour as the active background.
	-->
	<div class="mx-auto mb-3 flex w-full max-w-5xl flex-wrap gap-1.5">
		{#each CATEGORIES as cat (cat.id)}
			{@const on = enabled.has(cat.id)}
			<button
				type="button"
				onclick={() => toggle(cat.id)}
				aria-pressed={on}
				class="flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ring-1 transition focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-sky-300"
				style:background={on ? cat.color : '#ffffff'}
				style:color={on ? '#ffffff' : '#475569'}
				style:--tw-ring-color={on ? cat.color : '#e2e8f0'}
			>
				<span aria-hidden="true">{cat.emoji}</span>
				<span>{cat.label}</span>
			</button>
		{/each}
	</div>

	<!--
		MapLibre's container needs a sized, positioned element. We give
		it the flex-1 box directly with an inline `position: relative`
		so MapLibre's defensive "set position if static" code (which
		runs an inline style and would otherwise beat Tailwind's class)
		can't make it relative-with-no-size. Inline style also keeps
		the height intact regardless of CSS-load timing.
	-->
	<div
		bind:this={container}
		class="mx-auto w-full max-w-5xl flex-1 overflow-hidden rounded-3xl bg-sky-100 shadow-inner ring-1 ring-slate-200"
		style="position: relative; min-height: 70vh"
	>
		{#if tilesMissing}
			<div class="absolute inset-0 flex items-center justify-center bg-white/80 px-8 text-center">
				<div class="max-w-md">
					<p class="text-2xl">🗺️</p>
					<h2 class="mt-3 text-xl font-medium text-slate-800">No map tiles yet</h2>
					<p class="mt-2 text-sm text-slate-500">
						The PMTiles file isn't on the box yet. Run <code
							class="rounded bg-slate-200 px-1.5 py-0.5 font-mono text-xs">make tiles</code
						> on the host to fetch a planet basemap.
					</p>
				</div>
			</div>
		{/if}
	</div>
</main>
