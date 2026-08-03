/**
 * A statement or tax document tile.
 *
 * This component is built and currently renders nothing, and that is the
 * correct outcome rather than wasted work.
 *
 * Nothing in this backend generates a statement or issues a tax form. So the
 * screen ships both sections **absent** — not empty, not disabled, not showing
 * "No documents yet". The distinction matters more here than anywhere else on
 * the screen: an empty tax-document section is itself a claim. "No form for
 * 2025" asserts that a threshold determination was made and came back negative,
 * and a seller who reads that may reasonably conclude they have no filing
 * obligation. Nothing in this system performs that determination.
 *
 * The tile exists so that when statement generation is built, the surface it
 * needs already exists and already refuses to invent a name, a year, or an
 * availability date. Every field is required; there is no default that would
 * let a caller render a plausible-looking document that does not exist.
 *
 * `DocumentSection` below enforces the absence rule structurally: given an
 * empty list it returns null, so a caller cannot accidentally render a heading
 * with nothing under it.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { paymentsLight } from "../../theme/paymentsLight";

export type PaymentDocument = {
  /** The backend's own id. Never synthesised from a year or an index. */
  id: string;
  /** The real document name as issued. */
  title: string;
  /** e.g. "PDF · 24 KB" or the issue date — whatever the backend actually knows. */
  meta: string;
};

export type DocumentTileProps = {
  document: PaymentDocument;
  onOpen: (document: PaymentDocument) => void;
};

export function DocumentTile({ document, onOpen }: DocumentTileProps) {
  return (
    <Pressable
      style={styles.tile}
      onPress={() => onOpen(document)}
      accessibilityRole="button"
      accessibilityLabel={`${document.title}, ${document.meta}`}
      accessibilityHint="Opens this document"
    >
      <View style={styles.icon}>
        <Text style={styles.iconGlyph} allowFontScaling={false}>
          ▤
        </Text>
      </View>
      <View style={styles.body}>
        <Text style={styles.title} allowFontScaling numberOfLines={2}>
          {document.title}
        </Text>
        <Text style={styles.meta} allowFontScaling numberOfLines={1}>
          {document.meta}
        </Text>
      </View>
    </Pressable>
  );
}

export type DocumentSectionProps = {
  heading: string;
  documents: readonly PaymentDocument[];
  onOpen: (document: PaymentDocument) => void;
};

/**
 * A whole documents section, or nothing at all.
 *
 * Returning null on an empty list is the enforcement of the rule in the module
 * docstring — it makes "absent, not empty" the path of least resistance rather
 * than something a future caller has to remember.
 */
export function DocumentSection({ heading, documents, onOpen }: DocumentSectionProps) {
  if (!documents.length) return null;
  return (
    <View style={styles.section}>
      <Text style={styles.sectionHeading} accessibilityRole="header" allowFontScaling>
        {heading}
      </Text>
      {documents.map((document) => (
        <DocumentTile key={document.id} document={document} onOpen={onOpen} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  section: {
    marginTop: paymentsLight.space.section,
    paddingHorizontal: paymentsLight.space.gutter,
    gap: 8
  },
  sectionHeading: {
    color: paymentsLight.text.primary,
    fontSize: 15,
    fontWeight: "700",
    marginBottom: 4
  },
  tile: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    minHeight: paymentsLight.size.tapTarget,
    padding: 12,
    borderRadius: paymentsLight.radius.control,
    backgroundColor: paymentsLight.document.tileBg,
    borderWidth: 1,
    borderColor: paymentsLight.document.tileBorder
  },
  icon: {
    width: paymentsLight.size.iconCircle,
    height: paymentsLight.size.iconCircle,
    borderRadius: paymentsLight.radius.iconCircle,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: paymentsLight.bg.card,
    borderWidth: 1,
    borderColor: paymentsLight.document.tileBorder
  },
  iconGlyph: {
    fontSize: 15,
    color: paymentsLight.document.icon
  },
  body: {
    flex: 1
  },
  title: {
    color: paymentsLight.document.title,
    fontSize: 14,
    fontWeight: "600"
  },
  meta: {
    marginTop: 2,
    color: paymentsLight.document.meta,
    fontSize: 12
  }
});
