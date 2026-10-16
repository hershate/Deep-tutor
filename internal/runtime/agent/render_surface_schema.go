package agent

import "encoding/json"

var surfaceSchema = json.RawMessage(`{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "spec_version",
    "body"
  ],
  "properties": {
    "spec_version": {
      "type": "integer",
      "enum": [
        1
      ]
    },
    "title": {
      "type": "string",
      "maxLength": 200
    },
    "body": {
      "type": "array",
      "minItems": 1,
      "maxItems": 200,
      "items": {
        "$ref": "#/$defs/node"
      }
    }
  },
  "$defs": {
    "node": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "kind"
      ],
      "properties": {
        "kind": {
          "type": "string",
          "enum": [
            "text",
            "markdown",
            "row",
            "stack",
            "kv",
            "meter",
            "pip",
            "divider",
            "field",
            "choice_card",
            "editable_text",
            "diff_preview",
            "button",
            "map",
            "comparison",
            "dashboard"
          ]
        },
        "value": {
          "type": "string",
          "maxLength": 4000
        },
        "key": {
          "type": "string",
          "maxLength": 200
        },
        "label": {
          "type": "string",
          "maxLength": 200
        },
        "tone": {
          "type": "string",
          "enum": [
            "dim",
            "strong",
            "ok",
            "warn",
            "err",
            "accent"
          ]
        },
        "progress": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "actionId": {
          "type": "string",
          "pattern": "^[a-zA-Z0-9_-]{1,64}$"
        },
        "children": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/node"
          }
        },
        "field": {
          "$ref": "#/$defs/field"
        },
        "diff": {
          "$ref": "#/$defs/diff"
        },
        "dataset": {
          "$ref": "#/$defs/dataset"
        }
      }
    },
    "diff": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "before",
        "after"
      ],
      "properties": {
        "before": {
          "type": "string",
          "maxLength": 4000
        },
        "after": {
          "type": "string",
          "maxLength": 4000
        }
      }
    },
    "field": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "type"
      ],
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "string",
            "number",
            "bool",
            "choice",
            "multichoice"
          ]
        },
        "required": {
          "type": "boolean"
        },
        "default": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "number"
            },
            {
              "type": "boolean"
            },
            {
              "type": "array",
              "items": {
                "type": "string"
              }
            }
          ]
        },
        "min": {
          "type": "number"
        },
        "max": {
          "type": "number"
        },
        "max_length": {
          "type": "integer",
          "minimum": 0,
          "maximum": 4000
        },
        "options": {
          "type": "array",
          "minItems": 1,
          "maxItems": 16,
          "items": {
            "$ref": "#/$defs/option"
          }
        }
      }
    },
    "option": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "value",
        "label"
      ],
      "properties": {
        "value": {
          "type": "string",
          "pattern": "^[a-zA-Z0-9_-]{1,64}$"
        },
        "label": {
          "type": "string",
          "maxLength": 200
        },
        "description": {
          "type": "string",
          "maxLength": 4000
        },
        "preview": {
          "type": "string",
          "maxLength": 4000
        },
        "group": {
          "type": "string",
          "maxLength": 200
        },
        "location": {
          "$ref": "#/$defs/location"
        },
        "price": {
          "type": "number",
          "minimum": 0
        },
        "currency": {
          "type": "string",
          "maxLength": 8
        },
        "facts": {
          "type": "array",
          "minItems": 0,
          "maxItems": 12,
          "items": {
            "$ref": "#/$defs/fact"
          }
        },
        "image": {
          "$ref": "#/$defs/image"
        }
      }
    },
    "location": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "latitude",
        "longitude"
      ],
      "properties": {
        "latitude": {
          "type": "number",
          "minimum": -85,
          "maximum": 85
        },
        "longitude": {
          "type": "number",
          "minimum": -180,
          "maximum": 180
        }
      }
    },
    "fact": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "label",
        "value"
      ],
      "properties": {
        "label": {
          "type": "string",
          "maxLength": 200
        },
        "value": {
          "type": "string",
          "maxLength": 4000
        }
      }
    },
    "dataset": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "categories",
        "series"
      ],
      "properties": {
        "categories": {
          "type": "array",
          "minItems": 1,
          "maxItems": 64,
          "items": {
            "type": "string",
            "maxLength": 200
          }
        },
        "series": {
          "type": "array",
          "minItems": 1,
          "maxItems": 8,
          "items": {
            "$ref": "#/$defs/series"
          }
        }
      }
    },
    "series": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "id",
        "label",
        "values"
      ],
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-zA-Z0-9_-]{1,64}$"
        },
        "label": {
          "type": "string",
          "maxLength": 200
        },
        "unit": {
          "type": "string",
          "maxLength": 200
        },
        "values": {
          "type": "array",
          "minItems": 1,
          "maxItems": 64,
          "items": {
            "type": "number"
          }
        }
      }
    },
    "image": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "path",
        "alt"
      ],
      "properties": {
        "path": {
          "type": "string",
          "minLength": 1,
          "maxLength": 1024,
          "description": "Existing workspace-relative raster image path; no URLs, absolute paths or traversal."
        },
        "source": {"type":"string","maxLength":4096,"description":"Original HTTP(S) source page returned by import_web_image; displayed as an attribution link."},
        "alt": {
          "type": "string",
          "minLength": 1,
          "maxLength": 200
        }
      }
    }
  }
}`)
