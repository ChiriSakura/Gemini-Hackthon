/**
 * English Display Localization Helper for StageState Frontend.
 *
 * Feature & Use Cases:
 * - Maps known Chinese seed strings from locked mock/cached branch JSONs
 *   (`branch_v1`, `branch_v2`, `branch_v2_cached`) into crisp English labels
 *   for NYC Hackathon judges while preserving any custom English text verbatim.
 */
import type { BranchState, PropRegistryItem, Scene } from '../types/schema';

const HAS_CJK = /[\u4e00-\u9fff]/;

const PROP_NAME_EN: Record<string, string> = {
  red_umbrella: 'Red Umbrella',
  crystal_vase: 'Crystal Vase',
  star_token: 'Starlight Token',
};

const BRANCH_LABEL_EN: Record<string, string> = {
  branch_v1: 'BE · Rainy Flight & Shattered Vase (Cached Baseline)',
  branch_v2: 'HE · Starlight Trust & Shared Umbrella (Forked Scene 2)',
  branch_v2_cached: 'HE · Starlight Trust & Shared Umbrella (Cached Fork)',
};

const SCENE_EN_BY_BRANCH: Record<string, Record<number, { title: string; narration: string }>> = {
  branch_v1: {
    1: {
      title: 'Rainy Encounter',
      narration:
        'Under warm dim light, Lia stands on the left holding her red umbrella while Nyx the starry black cat watches the blue-rose crystal vase on the center table.',
    },
    2: {
      title: 'Startled & Shattered',
      narration:
        'A sudden thunderclap startles Nyx as Lia reaches out; the cat leaps across the table and knocks the crystal vase onto the cold wet floor.',
    },
    3: {
      title: 'Cautious Distance',
      narration:
        'Under cold blue rainlight, shattered glass and blue rose petals remain on the floor as Nyx crouches warily in the far corner.',
    },
    4: {
      title: 'BE · Left in the Rain',
      narration:
        'Nyx flees into the downpour while Camera B frames Lia standing alone beneath her red umbrella beside the broken glass shards.',
    },
  },
  branch_v2: {
    1: {
      title: 'Rainy Encounter (Reused)',
      narration:
        'Under warm dim light, Lia stands on the left holding her red umbrella while Nyx watches the intact crystal vase on the center table.',
    },
    2: {
      title: 'Gentle Starlight Offering',
      narration:
        'Lia lowers her red umbrella, kneels gently, and offers the glowing starlight token; Nyx approaches calmly and the crystal vase stays intact!',
    },
    3: {
      title: 'Causal Reversal · Guardian',
      narration:
        'The conservatory floor stays completely clean with no glass shards; Nyx curls warmly beside Lia guarding the glowing starlight token.',
    },
    4: {
      title: 'HE · Starlight Snow & Shared Umbrella',
      narration:
        'Warm golden starlight and falling snow fill the conservatory as Lia shelters Nyx under her red umbrella beside the intact crystal vase.',
    },
  },
};

/**
 * Return an English display label for a branch.
 */
export function getBranchLabelEn(branch: BranchState): string {
  if (!HAS_CJK.test(branch.branch_label)) return branch.branch_label;
  if (BRANCH_LABEL_EN[branch.branch_id]) return BRANCH_LABEL_EN[branch.branch_id];
  if (branch.forked_from_scene) {
    return `Forked Branch · Scene ${branch.forked_from_scene} Rewrite`;
  }
  return branch.branch_id;
}

/**
 * Return English title and narration for a scene when seed JSON contains CJK.
 */
export function getSceneTextEn(
  branchId: string,
  scene: Scene,
): { title: string; narration: string } {
  const key = branchId.startsWith('branch_v2') ? 'branch_v2' : branchId;
  const preset = SCENE_EN_BY_BRANCH[key]?.[scene.scene_id];
  const title = HAS_CJK.test(scene.title)
    ? (preset?.title ?? `Scene ${scene.scene_id}`)
    : scene.title;
  const narration = HAS_CJK.test(scene.narration)
    ? (preset?.narration ?? `Scene ${scene.scene_id} (${scene.lighting}, ${scene.vocal_emotion})`)
    : scene.narration;
  return { title, narration };
}

/**
 * Return an English display name for a stage prop.
 */
export function getPropNameEn(propId: string, item?: PropRegistryItem): string {
  if (item && !HAS_CJK.test(item.name)) return item.name;
  return PROP_NAME_EN[propId] ?? propId.replace(/_/g, ' ');
}
