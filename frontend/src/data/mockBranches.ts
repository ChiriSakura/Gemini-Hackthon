/**
 * Local Mock Branches Loader for StageState Frontend.
 *
 * Feature & Use Cases:
 * - Directly imports the locked `shared/mock_branch_1.json` (BE) and
 *   `shared/mock_branch_2.json` (HE) contracts so the Three.js 2.5D stage
 *   can be developed, tested, and demonstrated offline without the backend.
 */
import mockBranch1Raw from '../../../shared/mock_branch_1.json';
import mockBranch2Raw from '../../../shared/mock_branch_2.json';
import type { BranchState } from '../types/schema';
import { sanitizeBranchToEn } from '../utils/displayLocale';

export const MOCK_BRANCH_1 = sanitizeBranchToEn(
  mockBranch1Raw as unknown as BranchState,
);
export const MOCK_BRANCH_2 = sanitizeBranchToEn(
  mockBranch2Raw as unknown as BranchState,
);

export const INITIAL_MOCK_BRANCHES: BranchState[] = [
  MOCK_BRANCH_1,
  MOCK_BRANCH_2,
];
