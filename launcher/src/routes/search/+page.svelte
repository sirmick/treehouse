<script lang="ts">
	import { activeHost, hostnames } from '$lib/config';

	type Hit = {
		id: string;
		title: string;
		snippet: string;
		source: string;
		kind: string;
		deeplink_book?: string;
		deeplink_path?: string;
	};

	// Source → display label + emoji + which kids hostname routes
	// clicks for it. Each kiwix-served source has its own FQDN now,
	// so search-result links land on the right "app" host instead
	// of cross-host into the Wikipedia FQDN.
	const SOURCE_META: Record<
		string,
		{ label: string; emoji: string; host: keyof typeof hostnames }
	> = {
		wikipedia: { label: 'Wikipedia', emoji: '📚', host: 'wikipedia' },
		wiktionary: { label: 'Wiktionary', emoji: '📖', host: 'dictionary' },
		vikidia: { label: 'Vikidia', emoji: '🌱', host: 'vikidia' }
	};

	let query = $state('');
	let hits = $state<Hit[]>([]);
	let busy = $state(false);
	let error = $state<string | null>(null);
	let lastQuery = $state('');

	function urlFor(hit: Hit): string {
		// Each source routes through its own FQDN (wiki-kids,
		// dictionary-kids, vikidia-kids). Sources we don't know fall
		// back to the wikipedia FQDN as a best effort.
		if (!hit.deeplink_book || !hit.deeplink_path) return '#';
		const meta = SOURCE_META[hit.source];
		const host = activeHost(meta?.host ?? 'wikipedia');
		return `http://${host}/content/${hit.deeplink_book}/${hit.deeplink_path}`;
	}

	function badgeFor(source: string) {
		return SOURCE_META[source] ?? { label: source, emoji: '📄' };
	}

	async function search(e: SubmitEvent) {
		e.preventDefault();
		const q = query.trim();
		if (!q) return;
		busy = true;
		error = null;
		lastQuery = q;
		try {
			const r = await fetch('/api/search', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ q, limit: 20 })
			});
			if (!r.ok) {
				throw new Error(`search failed: ${r.status}`);
			}
			const data = await r.json();
			hits = (data.hits ?? []) as Hit[];
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
			hits = [];
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head>
	<title>Search · Treehouse</title>
</svelte:head>

<main class="min-h-screen px-4 py-8 sm:px-8 sm:py-12">
	<div class="mx-auto max-w-3xl">
		<header class="mb-8 flex items-center justify-between">
			<a
				href="/"
				class="text-sm font-medium text-slate-500 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-sky-300"
			>
				← Home
			</a>
			<span class="text-2xl" aria-hidden="true">🔍</span>
		</header>

		<form onsubmit={search} class="mb-6">
			<label for="q" class="sr-only">What would you like to learn about?</label>
			<input
				id="q"
				type="search"
				bind:value={query}
				placeholder="What would you like to learn about?"
				class="w-full rounded-3xl border-2 border-slate-200 bg-white px-6 py-4 text-lg shadow-sm transition focus:border-sky-300 focus:outline-none focus:ring-4 focus:ring-sky-200 sm:text-xl"
				autofocus
				autocomplete="off"
			/>
		</form>

		{#if busy}
			<p class="text-center text-slate-500">Looking…</p>
		{:else if error}
			<p class="text-center text-red-600">{error}</p>
		{:else if lastQuery && hits.length === 0}
			<p class="text-center text-slate-500">
				Hmm, nothing here for "<strong>{lastQuery}</strong>". Try different words?
			</p>
		{:else if hits.length > 0}
			<ul class="space-y-3">
				{#each hits as hit (hit.id)}
					<li>
						<a
							href={urlFor(hit)}
							class="block rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200 transition hover:bg-sky-50 hover:shadow-md focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-sky-300"
						>
							<div class="flex items-baseline justify-between gap-3">
								<h2 class="text-lg font-medium text-slate-800 sm:text-xl">
									{hit.title}
								</h2>
								<span class="shrink-0 text-xs font-medium tracking-wide text-slate-400">
									<span aria-hidden="true">{badgeFor(hit.source).emoji}</span>
									{badgeFor(hit.source).label}
								</span>
							</div>
							<p class="mt-2 text-sm text-slate-600 sm:text-base">
								{hit.snippet}…
							</p>
						</a>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="mt-4 text-center text-sm text-slate-400">
				Search across the {Object.keys(SOURCE_META).length} libraries on the box.
			</p>
		{/if}
	</div>
</main>
