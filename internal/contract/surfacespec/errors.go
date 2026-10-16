package surfacespec

type Code string

const (
	CodeSpecVersionUnsupported Code = "surface.spec_version_unsupported"
	CodeSchemaInvalid          Code = "surface.schema_invalid"
	CodeLimitExceeded          Code = "surface.limit_exceeded"
	CodeComponentUnknown       Code = "surface.component_unknown"
	CodeValueInvalid           Code = "surface.value_invalid"
	CodeExpired                Code = "surface.expired"
)

var (
	ErrSpecVersionUnsupported = &Error{Code: CodeSpecVersionUnsupported}
	ErrSchemaInvalid          = &Error{Code: CodeSchemaInvalid}
	ErrLimitExceeded          = &Error{Code: CodeLimitExceeded}
	ErrComponentUnknown       = &Error{Code: CodeComponentUnknown}
	ErrValueInvalid           = &Error{Code: CodeValueInvalid}
	ErrExpired                = &Error{Code: CodeExpired}
)

type Error struct {
	Code Code
	Path string
}

func (e *Error) Error() string {
	if e.Path == "" {
		return string(e.Code)
	}
	return string(e.Code) + " at " + e.Path
}

func (e *Error) RefusalCode() string { return string(e.Code) }

func (e *Error) Is(target error) bool {
	t, ok := target.(*Error)
	return ok && t.Code == e.Code && t.Path == ""
}

func fail(code Code, path string) *Error { return &Error{Code: code, Path: path} }
