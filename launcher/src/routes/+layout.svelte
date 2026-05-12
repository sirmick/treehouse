<script lang="ts">
	import { onMount } from 'svelte';
	import './layout.css';
	import favicon from '$lib/assets/favicon.svg';
	import { hostnames, networkMode } from '$lib/config';

	let { children } = $props();

	// Mount the same top bar used on kiwix vhosts (injected there via
	// nginx sub_filter). On launcher pages we control the document, so
	// we can just set the config and load the script — no proxy tricks.
	onMount(() => {
		if (window.__TREEHOUSE_TOPBAR__) return;
		window.__TREEHOUSE_TOPBAR__ = { hostnames, mode: networkMode };
		const s = document.createElement('script');
		s.src = '/topbar.js';
		s.defer = true;
		document.head.appendChild(s);
	});
</script>

<svelte:head><link rel="icon" href={favicon} /></svelte:head>
{@render children()}
