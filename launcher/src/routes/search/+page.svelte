<script lang="ts">
	import { onMount } from 'svelte';
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
	// clicks for it. Each content service has its own FQDN, so
	// search-result links land on the right "app" host instead of
	// cross-host into the wrong one.
	const SOURCE_META: Record<
		string,
		{ label: string; emoji: string; host: keyof typeof hostnames }
	> = {
		wikipedia: { label: 'Wikipedia', emoji: '📚', host: 'wikipedia' },
		wiktionary: { label: 'Wiktionary', emoji: '📖', host: 'dictionary' },
		vikidia: { label: 'Vikidia', emoji: '🌱', host: 'vikidia' },
		book: { label: 'Books', emoji: '📕', host: 'books' }
	};

	let query = $state('');
	let hits = $state<Hit[]>([]);
	let busy = $state(false);
	let error = $state<string | null>(null);
	let lastQuery = $state('');

	// Top bar (injected into kiwix pages) submits its search form to
	// /search?q=…, so picking up `q` here is how cross-app search works.
	onMount(() => {
		const q = new URL(window.location.href).searchParams.get('q')?.trim();
		if (q) {
			query = q;
			runQuery(q);
		}
	});

	async function runQuery(q: string) {
		busy = true;
		error = null;
		lastQuery = q;
		// Per-source parallel queries, then interleave. A single query
		// across the whole index lets Wikipedia (970k chunks) drown out
		// the smaller sources — books and Vikidia would never surface
		// for a generic term. 5 hits per source × 4 sources = 20 hits.
		const sources = Object.keys(SOURCE_META);
		const perSource = 5;
		try {
			const responses = await Promise.all(
				sources.map((src) =>
					fetch('/api/search', {
						method: 'POST',
						headers: { 'Content-Type': 'application/json' },
						body: JSON.stringify({ q, filter: `source = ${src}`, limit: perSource })
					}).then((r) => (r.ok ? r.json() : { hits: [] }))
				)
			);
			// Interleave: rank-1 from every source, then rank-2 from every
			// source, etc. Sources with no hits drop out naturally.
			const merged: Hit[] = [];
			for (let i = 0; i < perSource; i++) {
				for (const data of responses) {
					const hit = (data.hits ?? [])[i];
					if (hit) merged.push(hit as Hit);
				}
			}
			hits = merged;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
			hits = [];
		} finally {
			busy = false;
		}
	}

	function urlFor(hit: Hit): string {
		// Each source routes through its own FQDN. Books use calibre-web's
		// own search-results page (matches by title — calibre's book IDs
		// aren't in the ingest path); everything else is kiwix content.
		const meta = SOURCE_META[hit.source];
		const host = activeHost(meta?.host ?? 'wikipedia');
		if (hit.source === 'book') {
			if (!hit.deeplink_path) return '#';
			return `http://${host}/search/stored/?query=${encodeURIComponent(hit.deeplink_path)}`;
		}
		if (!hit.deeplink_book || !hit.deeplink_path) return '#';
		return `http://${host}/content/${hit.deeplink_book}/${hit.deeplink_path}`;
	}

	function badgeFor(source: string) {
		return SOURCE_META[source] ?? { label: source, emoji: '📄' };
	}

	async function search(e: SubmitEvent) {
		e.preventDefault();
		const q = query.trim();
		if (!q) return;
		await runQuery(q);
	}
</script>

<svelte:head>
	<title>Search · Treehouse</title>
</svelte:head>

<main class="min-h-screen px-4 py-4 sm:px-8 sm:py-6">
	<div class="mx-auto max-w-3xl">
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
