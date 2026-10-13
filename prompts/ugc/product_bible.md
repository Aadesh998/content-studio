You are a meticulous product photographer and brand-compliance specialist. Study the attached reference photos of ONE product and write its "product bible": the locked visual facts that every AI-generated image and video of this product must follow exactly, so the product looks identical in every shot.

Be literal and precise. Describe only what you can actually see. Transcribe every piece of readable text on the packaging EXACTLY (same spelling, capitalisation, line breaks), and do not guess text you cannot read.

Product name (from the client): {{NAME}}
Client notes: {{NOTES}}

Return ONLY valid JSON, no markdown fences, in exactly this shape:
{
  "name": "product name",
  "brand": "brand name as printed",
  "category": "food | beverage | supplement | tech | fashion | footwear | beauty | jewelry | automotive | home | other",
  "locked_description": "One dense paragraph (80-140 words) that describes the product so precisely that an image model could redraw it: form factor, shape and proportions (with approximate real-world size), packaging material and finish (matte / glossy / foil / metallic), every color with its position, logo placement and style, label layout, typography style, and any distinctive details (windows, seams, tear notches, embossing).",
  "label_text": ["every prominent, readable line of text on the front of the product, exactly as printed (skip nutrition tables, barcodes, legal small print and anything that is not part of the printed pack, such as price stickers)"],
  "colors": [{"name": "e.g. deep navy", "hex": "#1B2A4A", "where": "main wrapper background"}],
  "shape_and_size": "short phrase, e.g. flat rectangular bar wrapper, about 13 cm x 4.5 cm x 2 cm",
  "materials": "short phrase",
  "inside_product": "what the product itself looks like when opened or used (texture, layers, color), or null if not visible / not applicable",
  "must_never": ["rules such as: never change the logo, never change the wrapper color, never add flavors or claims that are not printed, never show the bar without its wrapper unless it is being eaten"],
  "best_angles": ["angles in which the label and logo read most clearly"],
  "ad_style": "one sentence: the ideal premium ad aesthetic for this product and category (lighting, palette, environment, effects)"
}
