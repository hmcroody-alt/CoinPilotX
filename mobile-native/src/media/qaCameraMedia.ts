import { cacheDirectory, EncodingType, makeDirectoryAsync, writeAsStringAsync } from "expo-file-system/legacy";
import { isQaSimulatorAuthEnabled } from "../session/qaSimulatorAuth";
import { nativeMediaAssetFromUri, NativeMediaAsset } from "./nativeMediaUpload";

const QA_CAMERA_IMAGE_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgF/ak4j4wAAAABJRU5ErkJggg==";

export function shouldEnableQaCameraMediaAutomation() {
  return isQaSimulatorAuthEnabled();
}

export async function createQaCameraImageAsset(): Promise<NativeMediaAsset> {
  const directory = `${cacheDirectory || ""}pulsesoc-qa/`;
  await makeDirectoryAsync(directory, { intermediates: true }).catch(() => undefined);
  const uri = `${directory}camera-studio-qa-image.png`;
  await writeAsStringAsync(uri, QA_CAMERA_IMAGE_BASE64, {
    encoding: EncodingType.Base64
  });
  return nativeMediaAssetFromUri(uri, "image", {
    name: "camera-studio-qa-image.png",
    mimeType: "image/png",
    width: 1,
    height: 1
  });
}
