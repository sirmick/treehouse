<script lang="ts">
	import { onMount } from 'svelte';
	import { geoNaturalEarth1, geoPath } from 'd3-geo';
	import { select } from 'd3-selection';
	import { zoom, type ZoomTransform } from 'd3-zoom';
	import { feature } from 'topojson-client';
	// world-atlas ships TopoJSON for countries at 110m resolution.
	// Bundled into the build (~150 KB) — no runtime fetch.
	import worldTopo from 'world-atlas/countries-110m.json';
	import type { Topology, GeometryCollection } from 'topojson-specification';
	import type { Feature, FeatureCollection, Geometry } from 'geojson';

	type CountryProps = { name: string };

	let svgEl: SVGSVGElement;
	let gEl: SVGGElement;
	let outerEl: HTMLDivElement;
	let hovered = $state<string | null>(null);

	onMount(() => {
		const topology = worldTopo as unknown as Topology;
		const countries = feature(
			topology,
			topology.objects.countries as GeometryCollection
		) as FeatureCollection<Geometry, CountryProps>;

		const draw = () => {
			const w = outerEl.clientWidth;
			const h = outerEl.clientHeight;

			// Natural Earth projection — gentler than Mercator at the
			// poles and more familiar than equirectangular. Centered.
			const projection = geoNaturalEarth1()
				.fitExtent([[20, 20], [w - 20, h - 20]], countries);
			const path = geoPath(projection);

			const svg = select(svgEl).attr('viewBox', `0 0 ${w} ${h}`);
			const g = select(gEl);

			g.selectAll('path.country')
				.data(countries.features)
				.join('path')
				.attr('class', 'country')
				.attr('d', (d) => path(d) ?? '')
				.attr('fill', '#e2f1f8')
				.attr('stroke', '#94a3b8')
				.attr('stroke-width', 0.5)
				.on('mouseenter', (_, d) => (hovered = d.properties?.name ?? null))
				.on('mouseleave', () => (hovered = null))
				.append('title')
				.text((d) => d.properties?.name ?? '');

			svg.call(
				zoom<SVGSVGElement, unknown>()
					.scaleExtent([1, 8])
					.translateExtent([
						[-w * 0.5, -h * 0.5],
						[w * 1.5, h * 1.5]
					])
					.on('zoom', (event) => {
						const t: ZoomTransform = event.transform;
						g.attr('transform', t.toString());
					})
			);
		};

		draw();
		const ro = new ResizeObserver(draw);
		ro.observe(outerEl);
		return () => ro.disconnect();
	});
</script>

<svelte:head>
	<title>Maps · Treehouse</title>
</svelte:head>

<main class="flex min-h-screen flex-col px-4 py-8 sm:px-8 sm:py-10">
	<header class="mx-auto mb-6 flex w-full max-w-5xl items-center justify-between">
		<a
			href="/"
			class="text-sm font-medium text-slate-500 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-sky-300"
		>
			← Home
		</a>
		<span class="text-2xl" aria-hidden="true">🗺️</span>
	</header>

	<div
		bind:this={outerEl}
		class="mx-auto w-full max-w-5xl flex-1 overflow-hidden rounded-3xl bg-sky-100 shadow-inner ring-1 ring-slate-200"
		style="min-height: 60vh"
	>
		<svg bind:this={svgEl} class="h-full w-full" role="img" aria-label="World map">
			<g bind:this={gEl}></g>
		</svg>
	</div>

	<p class="mx-auto mt-4 max-w-5xl text-center text-sm text-slate-500">
		{#if hovered}
			<span class="font-medium text-slate-800">{hovered}</span>
		{:else}
			Drag to pan, scroll or pinch to zoom.
		{/if}
	</p>
</main>
