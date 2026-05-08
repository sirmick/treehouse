<script lang="ts">
	import { activeHost } from '$lib/config';

	type Tile = {
		label: string;
		emoji: string;
		href?: string;
	};

	const tiles: Tile[] = [
		{ label: 'Search', emoji: '🔍', href: `http://${activeHost('search')}/` },
		{ label: 'Wikipedia', emoji: '📚', href: `http://${activeHost('wikipedia')}/` },
		{ label: 'Maps', emoji: '🗺️', href: `http://${activeHost('maps')}/` },
		{ label: 'Dictionary', emoji: '📖', href: `http://${activeHost('dictionary')}/` },
		{ label: 'Vikidia', emoji: '🌱', href: `http://${activeHost('vikidia')}/` },
		{ label: 'Videos', emoji: '🎬' },
		{ label: 'Khan Academy', emoji: '🎓' },
		{ label: 'Play', emoji: '🎨' },
		{ label: 'Chat', emoji: '💬' }
	];
</script>

<svelte:head>
	<title>Treehouse</title>
</svelte:head>

<main class="min-h-screen px-4 py-10 sm:px-8 sm:py-16">
	<div class="mx-auto max-w-5xl">
		<header class="mb-10 text-center sm:mb-14">
			<h1 class="text-5xl font-semibold tracking-tight text-slate-800 sm:text-6xl">
				Treehouse
			</h1>
			<p class="mt-3 text-lg text-slate-500 sm:mt-4 sm:text-xl">
				A friendly little corner of the internet.
			</p>
		</header>

		<div
			class="grid grid-cols-2 gap-4 sm:gap-5 md:grid-cols-3 md:gap-6 lg:grid-cols-4"
		>
			{#each tiles as tile (tile.label)}
				{#if tile.href}
					<a
						href={tile.href}
						class="group flex aspect-square flex-col items-center justify-center rounded-3xl bg-white shadow-sm ring-1 ring-slate-200 transition duration-150 hover:bg-sky-50 hover:shadow-md focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-sky-300 active:scale-95"
					>
						<span class="text-6xl sm:text-7xl" aria-hidden="true">{tile.emoji}</span>
						<span class="mt-3 text-lg font-medium text-slate-700 sm:mt-4 sm:text-xl">
							{tile.label}
						</span>
					</a>
				{:else}
					<div
						class="flex aspect-square flex-col items-center justify-center rounded-3xl bg-white/60 ring-1 ring-slate-200/60"
						aria-disabled="true"
					>
						<span class="text-6xl opacity-60 sm:text-7xl" aria-hidden="true">{tile.emoji}</span>
						<span class="mt-3 text-lg font-medium text-slate-400 sm:mt-4 sm:text-xl">
							{tile.label}
						</span>
						<span
							class="mt-1 text-xs font-semibold uppercase tracking-widest text-slate-400"
						>
							soon
						</span>
					</div>
				{/if}
			{/each}
		</div>
	</div>
</main>
