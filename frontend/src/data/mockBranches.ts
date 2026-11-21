/**
 * Local Mock Branches Loader for StageState Frontend (100% Native English).
 *
 * Feature & Use Cases:
 * - Seeds all 4 story arcs (`branch_v1` BE, `branch_v2` HE Starlight,
 *   `branch_v3_starlight_cached` Twist V3, and `branch_v4_umbrella_shield_cached`
 *   Shield V4) in clean idiomatic English so the 2.5D Three.js stage and
 *   4-branch preset switcher work seamlessly even when FastAPI is offline.
 */
import mockBranch1Raw from '../../../shared/mock_branch_1.json';
import mockBranch2Raw from '../../../shared/mock_branch_2.json';
import type { BranchState } from '../types/schema';
import { sanitizeBranchToEn } from '../utils/displayLocale';

export const MOCK_BRANCH_1: BranchState = sanitizeBranchToEn(
  mockBranch1Raw as unknown as BranchState,
);

export const MOCK_BRANCH_2: BranchState = sanitizeBranchToEn(
  mockBranch2Raw as unknown as BranchState,
);

export const MOCK_BRANCH_3: BranchState = {
  ...MOCK_BRANCH_2,
  branch_id: 'branch_v3_starlight_cached',
  branch_label: 'HE · Starlight Vigil & Shared Shelter (3rd Cached Variant)',
  parent_branch_id: 'branch_v1',
  forked_from_scene: 2,
  rewrite_summary:
    'Lia offers the glowing Starlight Keepsake in Scene 2 and preserves it alongside the intact Blue Rose Crystal Vase through Scene 4.',
  scenes: MOCK_BRANCH_2.scenes.map((sc) =>
    sc.scene_id === 3
      ? {
          ...sc,
          title: 'Starlight Vigil',
          narration:
            'Soft golden snow begins drifting against the glass panes as Nyx curls protectively beside the glowing Starlight Keepsake.',
          lighting: 'warm_gold_snow',
        }
      : sc,
  ),
};

export const MOCK_BRANCH_4: BranchState = {
  ...MOCK_BRANCH_1,
  branch_id: 'branch_v4_umbrella_shield_cached',
  branch_label: 'HE · Umbrella Shield & Intact Vase (4th Cached Variant)',
  parent_branch_id: 'branch_v1',
  forked_from_scene: 2,
  rewrite_summary:
    'In Scene 2, Lia swings her Crimson Vintage Umbrella in front of the center table to shield the Blue Rose Crystal Vase so it never shatters.',
  props_registry: {
    red_umbrella: {
      name: 'Crimson Vintage Umbrella',
      image_url: '/assets/actors/lia.png',
      state: 'held',
      origin: 'preset',
    },
    crystal_vase: {
      name: 'Blue Rose Crystal Vase',
      image_url: '/assets/props/crystal_vase.png',
      state: 'intact',
      origin: 'preset',
    },
  },
  scenes: MOCK_BRANCH_1.scenes.map((sc) => {
    if (sc.scene_id === 1) {
      return {
        ...sc,
        title: 'Rainy Encounter (Reused)',
        needs_rerender: false,
        reused_from_branch: 'branch_v1',
      };
    }
    if (sc.scene_id === 2) {
      return {
        ...sc,
        title: 'Umbrella Shield Saves Vase',
        narration:
          'When thunder strikes, Lia quickly angles her Crimson Vintage Umbrella around the center table; Nyx pauses safely and the Blue Rose Crystal Vase stays completely intact!',
        lighting: 'warm_transition',
        camera_shake: false,
        needs_rerender: true,
        reused_from_branch: null,
        environment_notes: 'clean_conservatory_floor_intact_crystal_vase_umbrella_shield',
        actors: [
          {
            id: 'lia',
            name: 'Lia',
            asset_url: '/assets/actors/lia.png',
            grid: [0.85, 1.05],
            facing: 'right',
            action: 'reach',
            holding_prop: 'red_umbrella',
          },
          {
            id: 'nyx',
            name: 'Nyx',
            asset_url: '/assets/actors/nyx.png',
            grid: [1.55, 1.05],
            facing: 'left',
            action: 'sit',
            holding_prop: null,
          },
        ],
        stage_props: [
          { id: 'crystal_vase', grid: [1, 1], on_floor: false, state: 'intact' },
        ],
      };
    }
    if (sc.scene_id === 3) {
      return {
        ...sc,
        title: 'Calm Conservatory Floor',
        narration:
          'No glass shards lie on the floor; Nyx steps closer toward Lia while the Blue Rose Crystal Vase glows calmly on the center table.',
        lighting: 'warm_transition',
        needs_rerender: true,
        reused_from_branch: null,
        environment_notes: 'clean_conservatory_floor_intact_crystal_vase',
        actors: [
          {
            id: 'lia',
            name: 'Lia',
            asset_url: '/assets/actors/lia.png',
            grid: [0.85, 1.12],
            facing: 'right',
            action: 'kneel',
            holding_prop: 'red_umbrella',
          },
          {
            id: 'nyx',
            name: 'Nyx',
            asset_url: '/assets/actors/nyx.png',
            grid: [1.3, 1.15],
            facing: 'left',
            action: 'walk',
            holding_prop: null,
          },
        ],
        stage_props: [
          { id: 'crystal_vase', grid: [1, 1], on_floor: false, state: 'intact' },
        ],
      };
    }
    return {
      ...sc,
      title: 'HE · Warm Shelter Together',
      narration:
        'Golden snowfall bathes the conservatory as Lia shelters Nyx under the Crimson Vintage Umbrella beside the intact Blue Rose Crystal Vase.',
      lighting: 'warm_gold_snow',
      needs_rerender: true,
      reused_from_branch: null,
      environment_notes: 'warm_golden_snow_intact_crystal_vase_shared_umbrella',
      actors: [
        {
          id: 'lia',
          name: 'Lia',
          asset_url: '/assets/actors/lia.png',
          grid: [0.95, 1.15],
          facing: 'right',
          action: 'shared_umbrella',
          holding_prop: 'red_umbrella',
        },
        {
          id: 'nyx',
          name: 'Nyx',
          asset_url: '/assets/actors/nyx.png',
          grid: [1.22, 1.2],
          facing: 'left',
          action: 'nuzzle',
          holding_prop: null,
        },
      ],
      stage_props: [
        { id: 'crystal_vase', grid: [1, 1], on_floor: false, state: 'intact' },
      ],
    };
  }),
};

export const INITIAL_MOCK_BRANCHES: BranchState[] = [
  MOCK_BRANCH_1,
  MOCK_BRANCH_2,
  MOCK_BRANCH_3,
  MOCK_BRANCH_4,
];
