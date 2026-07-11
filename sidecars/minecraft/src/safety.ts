import type { ForwardSafety } from './minecraft-adapter.js';

const PASSABLE_BLOCKS = new Set(['air', 'cave_air', 'void_air']);
const HAZARDOUS_BLOCKS = new Set([
  'fire',
  'soul_fire',
  'cactus',
  'campfire',
  'soul_campfire',
  'magma_block',
  'powder_snow',
  'nether_portal',
  'end_portal',
  'end_gateway',
  'sweet_berry_bush',
  'cobweb'
]);
const LIQUID_BLOCKS = new Set(['water', 'lava', 'bubble_column']);

export type DirectSteeringBlock = Readonly<{
  name?: unknown;
  boundingBox?: unknown;
  shapes?: unknown;
  isWaterlogged?: unknown;
}>;

export type DirectStepBlockResult =
  | 'safe'
  | Exclude<ForwardSafety['kind'], 'safe' | 'dimension_mismatch'>;

/**
 * Only literal air blocks are accepted around the bot. Plants, doors, snow,
 * climbables, and every unknown collision shape fail closed.
 */
export function classifyDirectSteeringSpace(
  block: DirectSteeringBlock | null
): DirectStepBlockResult {
  const common = classifyCommon(block);
  if (common !== 'safe') return common;
  const name = block?.name;
  if (typeof name !== 'string') return 'unloaded';
  return PASSABLE_BLOCKS.has(name) && block?.boundingBox === 'empty'
    ? 'safe'
    : 'blocked';
}

/**
 * Initial direct steering supports only a loaded, exact full-cube support
 * block at the current feet height. Partial blocks and unknown shapes are
 * treated as unsupported terrain, never as a step or drop.
 */
export function classifyDirectSteeringSupport(
  block: DirectSteeringBlock | null
): DirectStepBlockResult {
  const common = classifyCommon(block);
  if (common !== 'safe') return common;
  const name = block?.name;
  if (typeof name !== 'string') return 'unloaded';
  if (PASSABLE_BLOCKS.has(name)) return 'unsupported_drop';
  if (block?.boundingBox !== 'block' || !hasFullCubeShape(block.shapes)) {
    return 'unsupported_drop';
  }
  return 'safe';
}

function classifyCommon(
  block: DirectSteeringBlock | null
): DirectStepBlockResult {
  if (block === null) return 'unloaded';
  if (block.isWaterlogged === true) return 'liquid';
  const name = block.name;
  if (typeof name !== 'string') return 'unloaded';
  if (HAZARDOUS_BLOCKS.has(name)) return 'hazard';
  if (LIQUID_BLOCKS.has(name)) return 'liquid';
  return 'safe';
}

function hasFullCubeShape(value: unknown): boolean {
  if (!Array.isArray(value)) return false;
  return value.some((shape) => {
    return (
      Array.isArray(shape) &&
      shape.length === 6 &&
      shape.every((coordinate) => typeof coordinate === 'number') &&
      shape[0] === 0 &&
      shape[1] === 0 &&
      shape[2] === 0 &&
      shape[3] === 1 &&
      shape[4] === 1 &&
      shape[5] === 1
    );
  });
}
