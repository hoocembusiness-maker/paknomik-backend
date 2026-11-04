"""Add packaging keywords that are missing from the search-tag catalog.

Usage:
  python manage.py expand_search_tag_catalog
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from user.models import SearchTag
from user.search_vocabulary import invalidate_vocabulary_cache

# (english tag, french label). Skipped when either label already exists.
PACKAGING_TAGS = [
    ("PET bottle", "Flacon PET"),
    ("glass bottle", "Flacon verre"),
    ("plastic jar", "Pot plastique"),
    ("glass jar", "Pot verre"),
    ("plastic tube", "Tube plastique"),
    ("aluminium tube", "Tube aluminium"),
    ("doypack", "Sachet doypack"),
    ("stand-up pouch", "Sachet fond plat"),
    ("box pouch", "Sachet fond carré"),
    ("spout pouch", "Sachet à bec verseur"),
    ("vacuum pouch", "Sachet sous vide"),
    ("resealable pouch", "Sachet refermable"),
    ("stick pack", "Sachet stick"),
    ("flow wrap", "Flowpack"),
    ("BOPP film", "Film BOPP"),
    ("PE film", "Film PE"),
    ("PET film", "Film PET"),
    ("shrink film", "Film rétractable"),
    ("food film", "Film alimentaire"),
    ("shrink sleeve", "Sleeve rétractable"),
    ("sleeve label", "Manchon"),
    ("roll label", "Étiquette en rouleau"),
    ("wine label", "Étiquette vin"),
    ("cosmetic label", "Étiquette cosmétique"),
    ("pharma label", "Étiquette pharmaceutique"),
    ("shipping label", "Étiquette logistique"),
    ("in-mold label", "Étiquette IML"),
    ("screw cap", "Bouchon à vis"),
    ("sport cap", "Bouchon sport"),
    ("flip-top cap", "Bouchon flip-top"),
    ("disc-top cap", "Bouchon disc-top"),
    ("tamper-evident cap", "Bouchon inviolable"),
    ("lotion pump", "Pompe doseuse"),
    ("spray pump", "Pompe spray"),
    ("dropper", "Compte-gouttes"),
    ("lid", "Couvercle"),
    ("peelable lid", "Opercule pelable"),
    ("aluminium lid", "Opercule aluminium"),
    ("food tray", "Barquette alimentaire"),
    ("PET tray", "Barquette PET"),
    ("blister pack", "Blister"),
    ("pharma blister", "Blister pharmaceutique"),
    ("carton sleeve", "Étui carton"),
    ("medicine carton", "Étui médicament"),
    ("folding carton", "Boîte pliante"),
    ("cosmetic box", "Boîte cosmétique"),
    ("pastry box", "Boîte pâtisserie"),
    ("gift box", "Coffret"),
    ("RSC carton", "Caisse américaine"),
    ("corrugated board", "Carton ondulé"),
    ("paperboard", "Carton compact"),
    ("microflute", "Microcannelure"),
    ("kraft paper", "Papier kraft"),
    ("coated paper", "Papier couché"),
    ("tissue paper", "Papier de soie"),
    ("shopping bag", "Sac boutique"),
    ("flat handle bag", "Sac cabas"),
    ("sos bag", "Sac SOS"),
    ("cup", "Gobelet"),
    ("yogurt pot", "Pot de yaourt"),
    ("yogurt lid", "Opercule yaourt"),
    ("pail", "Seau"),
    ("jerrycan", "Bidon"),
    ("drum", "Fût"),
    ("pallet", "Palette"),
    ("edge protector", "Coin de protection"),
    ("adhesive tape", "Ruban adhésif"),
    ("strapping", "Cerclage"),
    ("pallet cover", "Housse de palette"),
    ("hood film", "Film coiffe"),
    ("layer pad", "Intercalaire"),
    ("reel", "Bobine"),
    ("airless bottle", "Flacon airless"),
    ("cosmetic bottle", "Flacon cosmétique"),
    ("roll-on bottle", "Roll-on"),
    ("ampoule", "Ampoule"),
    ("coffee capsule", "Capsule café"),
    ("pod", "Dosette"),
    ("carton brick", "Brique alimentaire"),
    ("beverage can", "Canette"),
    ("tin can", "Boîte de conserve"),
    ("jar", "Bocal"),
    ("clamshell", "Boîte clamshell"),
    ("bubble wrap", "Film bulle"),
    ("honeycomb board", "Carton nid d'abeille"),
    ("molded pulp", "Pulpe moulée"),
    ("cushioning", "Cale de calage"),
    ("insulated packaging", "Emballage isotherme"),
    ("POS display", "PLV"),
    ("barcode", "Code-barres"),
    ("hot foil", "Dorure à chaud"),
    ("lamination", "Pelliculage"),
    ("varnish", "Vernis"),
    ("flexo printing", "Flexographie"),
    ("digital printing", "Impression numérique"),
    ("offset printing", "Impression offset"),
    ("screen printing", "Sérigraphie"),
    ("PET preform", "Préforme PET"),
    ("heat seal", "Scellage"),
    ("thermoforming", "Thermoformage"),
    ("stretch blow molding", "Étire-soufflage"),
    ("aluminium foil", "Feuille d'aluminium"),
    ("laminate", "Complexe multicouche"),
    ("resealable zip", "Zip refermable"),
    ("pouring spout", "Bec verseur"),
    ("transparent window", "Fenêtre transparente"),
    ("cellophane", "Cellophane"),
    ("overwrap", "Suremballage"),
    ("single wall board", "Simple cannelure"),
    ("double wall board", "Double cannelure"),
    ("hot melt adhesive", "Colle hot melt"),
    ("PP strapping", "Feuillard PP"),
    ("date coding", "Marquage de lot"),
    ("tamper-evident ring", "Bague d'inviolabilité"),
    ("dispenser", "Doseur"),
    ("euro slot", "Trou européen"),
    ("carrier handle", "Poignée"),
]


class Command(BaseCommand):
    help = "Add missing packaging keywords to the search-tag catalog."

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        for tag, tag_fr in PACKAGING_TAGS:
            exists = SearchTag.objects.filter(
                Q(tag__iexact=tag) | Q(tag_fr__iexact=tag_fr)
            ).exists()
            if exists:
                skipped += 1
                continue
            SearchTag.objects.create(
                tag=tag,
                tag_fr=tag_fr,
                tag_group="Packaging",
                industry_name="Emballage",
                search_weight=8,
            )
            created += 1

        invalidate_vocabulary_cache()
        self.stdout.write(f"created: {created}")
        self.stdout.write(f"skipped: {skipped}")
        self.stdout.write(f"catalog: {SearchTag.objects.count()}")
