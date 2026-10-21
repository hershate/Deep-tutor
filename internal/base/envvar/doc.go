// Package envvar resolves the environment variables the product reads.
//
// The product was renamed from Reasonix to Deep-tutor, and the variables a
// user sets are part of that name. Each renamed variable therefore has two
// spellings: DEEPTUTOR_HOME is the current one, and REASONIX_HOME is the one
// a machine that configured it before the rename still carries.
//
// The current spelling wins when both are set; the old one is the fallback.
// The other order would be wrong in both directions. Preferring the old one
// lets a stale value overrule a deliberate new one — a user who moved their
// home to another volume and set the new name would find the old path back.
// Dropping the old one takes the configuration away from every machine that
// set it, including the machines that set it precisely to keep their data
// off the system volume.
package envvar
