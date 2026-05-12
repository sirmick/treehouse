// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
import type { hostnames, NetworkMode } from '$lib/config';

declare global {
	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		// interface PageState {}
		// interface Platform {}
	}

	interface Window {
		// Shared top bar config (launcher pages set it directly in +layout;
		// kiwix pages get it injected inline by nginx sub_filter).
		__TREEHOUSE_TOPBAR__?: { hostnames: typeof hostnames; mode: NetworkMode };
	}
}

export {};
