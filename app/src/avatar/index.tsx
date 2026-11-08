import { HelmetAvatar } from "./Helmet";
import type { AvatarProps } from "./types";

export function Avatar(props: AvatarProps) {
  return <HelmetAvatar {...props} />;
}
export type { AvatarProps, AvatarState, AvatarTone } from "./types";
