/**
 * English Display Localization & Sanitization Helper for StageState Frontend.
 *
 * Feature & Use Cases:
 * - Maps known Chinese seed strings from cached backend branch JSONs
 *   (`branch_v1`, `branch_v2`, `branch_v2_cached`, `branch_v3*`) into crisp
 *   English labels for NYC Hackathon judges while preserving custom English text.
 * - Provides deep `sanitizeBranchToEn()` and `sanitizeRulesToEn()` guards so no
 *   CJK text from backend responses, QA tooltips, or contradiction warnings can
 *   ever leak into the English-only UI.
 */
import type {
  BranchState,
  GestureIntent,
  PropRegistryItem,
  Scene,
  SceneMedia,
  VocalEmotion,
} from '../types/schema';

const HAS_CJK = /[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/;

const PROP_NAME_EN: Record<string, string> = {
  red_umbrella: 'Red Umbrella',
  crystal_vase: 'Crystal Vase',
  star_token: 'Starlight Token',
};

const ACTOR_NAME_EN: Record<string, string> = {
  lia: 'Lia',
  nyx: 'Nyx',
};

const BRANCH_LABEL_EN: Record<string, string> = {
  branch_v1: 'BE · Rainy Flight & Shattered Vase (Cached Baseline)',
  branch_v2: 'HE · Starlight Trust & Shared Umbrella (Forked Scene 2)',
  branch_v2_cached: 'HE · Starlight Trust & Shared Umbrella (Cached Fork)',
  branch_v3_starlight_cached: 'HE · Golden Snow & Starlight Trust (Cached Variant)',
};

const FALLBACK_RULES_EN: string[] = [
  'Once crystal_vase is broken, shards remain visible on the floor in all subsequent scenes.',
  'Actors must keep consistent identity, outfit, and spatial left/right placement across Camera A & B.',
  'Newly introduced props must persist in props_registry and subsequent scenes unless explicitly removed.',
];

const SCENE_EN_BY_BRANCH: Record<
  string,
  Record<number, { title: string; narration: string }>
> = {
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
 * Ensure an arbitrary UI string is English-only, falling back when CJK appears.
 */
export function sanitizeTextToEn(
  value: string | null | undefined,
  fallback: string,
): string {
  if (!value || !value.trim()) return fallback;
  if (!HAS_CJK.test(value)) return value;
  return fallback;
}

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
  const key =
    branchId.startsWith('branch_v2') || branchId.startsWith('branch_v3')
      ? 'branch_v2'
      : branchId;
  const preset = SCENE_EN_BY_BRANCH[key]?.[scene.scene_id];
  const title = HAS_CJK.test(scene.title)
    ? (preset?.title ?? `Scene ${scene.scene_id}`)
    : scene.title;
  const narration = HAS_CJK.test(scene.narration)
    ? (preset?.narration ??
      `Scene ${scene.scene_id} (${scene.lighting}, ${sanitizeTextToEn(scene.vocal_emotion, 'neutral')})`)
    : scene.narration;
  return { title, narration };
}

/**
 * Return an English display name for a stage prop.
 */
export function getPropNameEn(propId: string, item?: PropRegistryItem): string {
  if (item && !HAS_CJK.test(item.name)) return item.name;
  return (
    PROP_NAME_EN[propId] ??
    propId
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (ch) => ch.toUpperCase())
  );
}

/**
 * Normalize a `SceneMedia` payload so QA item tooltips never display CJK.
 */
export function sanitizeSceneMediaToEn(
  media: SceneMedia,
  sceneId: number,
): SceneMedia {
  if (!media.qa_result?.items) return media;
  return {
    ...media,
    qa_result: {
      ...media.qa_result,
      items: media.qa_result.items.map((item, idx) => {
        const camMatch = item.question.match(/^\s*(\[CAM\s+[AB]\])\s*/i);
        const prefix = camMatch ? `${camMatch[1].toUpperCase()} ` : '';
        return {
          ...item,
          question: HAS_CJK.test(item.question)
            ? `${prefix}Scene ${sceneId} continuity check #${idx + 1}`
            : item.question,
          reason: item.reason
            ? sanitizeTextToEn(
                item.reason,
                item.passed
                  ? 'Passed multimodal continuity check.'
                  : 'Failed multimodal continuity check.',
              )
            : item.reason,
        };
      }),
    },
  };
}

/**
 * Normalize active continuity rules to English-only text.
 */
export function sanitizeRulesToEn(rules: string[]): string[] {
  return rules.map((rule, idx) =>
    sanitizeTextToEn(
      rule,
      FALLBACK_RULES_EN[idx % FALLBACK_RULES_EN.length],
    ),
  );
}

/**
 * Deeply normalize an entire `BranchState` returned by the backend so zero CJK
 * characters leak into any visible label, narration, cue pill, or tooltip.
 */
export function sanitizeBranchToEn(branch: BranchState): BranchState {
  const sanitizedProps: Record<string, PropRegistryItem> = {};
  for (const [id, item] of Object.entries(branch.props_registry ?? {})) {
    sanitizedProps[id] = {
      ...item,
      name: getPropNameEn(id, item),
      state: sanitizeTextToEn(item.state, 'intact'),
    };
  }

  return {
    ...branch,
    branch_label: getBranchLabelEn(branch),
    rewrite_summary: branch.rewrite_summary
      ? sanitizeTextToEn(
          branch.rewrite_summary,
          branch.forked_from_scene
            ? `Causal rewrite applied starting at Scene ${branch.forked_from_scene}.`
            : 'Director rewrite applied.',
        )
      : null,
    contradiction_warning: branch.contradiction_warning
      ? sanitizeTextToEn(
          branch.contradiction_warning,
          'Continuity conflict detected against an earlier scene state. Click "Force Rewrite" to override.',
        )
      : null,
    props_registry: sanitizedProps,
    scenes: (branch.scenes ?? []).map((scene) => {
      const { title, narration } = getSceneTextEn(branch.branch_id, scene);
      return {
        ...scene,
        title,
        narration,
        vocal_emotion: sanitizeTextToEn(scene.vocal_emotion, 'neutral') as VocalEmotion,
        gesture_intent: sanitizeTextToEn(scene.gesture_intent, 'none') as GestureIntent,
        direction_hint: sanitizeTextToEn(scene.direction_hint, 'none'),
        audio_mood: sanitizeTextToEn(scene.audio_mood, 'ambient_conservatory'),
        environment_notes: sanitizeTextToEn(scene.environment_notes, 'clean_stage'),
        actors: (scene.actors ?? []).map((actor) => ({
          ...actor,
          name: HAS_CJK.test(actor.name)
            ? (ACTOR_NAME_EN[actor.id] ??
              actor.id.replace(/\b\w/g, (c) => c.toUpperCase()))
            : actor.name,
        })),
        qa_questions: (scene.qa_questions ?? []).map((q, idx) =>
          sanitizeTextToEn(q, `Scene ${scene.scene_id} continuity check #${idx + 1}`),
        ),
        media: sanitizeSceneMediaToEn(scene.media, scene.scene_id),
      };
    }),
  };
}
