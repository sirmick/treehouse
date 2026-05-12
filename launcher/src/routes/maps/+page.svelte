<script lang="ts">
	import { onMount } from 'svelte';
	import maplibregl, { type StyleSpecification } from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import { Protocol } from 'pmtiles';
	import { layers, namedFlavor } from '@protomaps/basemaps';

	let container: HTMLDivElement;
	let tilesMissing = $state(false);

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
