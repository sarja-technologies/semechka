// Mounts the two inspector components on their own, with the app's real stylesheet and nothing else of the app around them.
import { mount } from 'svelte';
import '../../../../src/app.css';
import Harness from './Harness.svelte';
mount(Harness, { target: document.getElementById('app')! });
