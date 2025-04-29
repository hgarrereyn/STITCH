
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::fmt;
use std::rc::Rc;
use std::str::FromStr;

use pest::iterators::Pair;
use pest::Parser;
use pest_derive::Parser;
use colored::Colorize;
use rustsat::instances::SatInstance;
use rustsat::types::Var;
use serde::{Serialize, Deserialize};

use crate::sampler::expr::FieldAttr;
use crate::sampler::util::{and_clause, or_clause};


// ────────────────────────────────────────────────────────────────────────────────
//  Grammar
// ────────────────────────────────────────────────────────────────────────────────

#[derive(Parser)]
#[grammar_inline = r#"
WHITESPACE = _{ " " | "\t" | "\r" | "\n" }
ident      = @{ (ASCII_ALPHA | "_") ~ (ASCII_ALPHANUMERIC | "_")* }
int_lit    = @{ "-"? ~ ASCII_DIGIT+ }

var_field    = { ident ~ "." ~ ident }
global_field = { "$" ~ "." ~ ident }
leaf         = { var_field | global_field }

variant_qual = { ident ~ "::" ~ ident }
variant_bare = { ident }

int_atom = _{ leaf | int_lit | "(" ~ int_expr ~ ")" }
int_expr =  { int_atom ~ (int_op ~ int_atom)* }
int_op   =  { "+" | "-" }

enum_expr = { leaf | variant_qual | variant_bare }

cmp_op = { "==" | "!=" | ">=" | ">" | "<=" | "<" }

bool_expr_cmp     = { int_expr ~ cmp_op ~ int_expr }
bool_expr_not     = { "not" ~ bool_expr }
bool_expr_enum_is = { enum_expr ~ "is" ~ enum_expr }

bool_atom = _{
    bool_expr_cmp
  | bool_expr_enum_is
  | "(" ~ bool_expr ~ ")"
}
bool_expr =  { bool_atom ~ (bool_op ~ bool_atom)* }
bool_op   =  { "and" | "or" }

assignment = { SOI ~ leaf ~ ":=" ~ (int_expr | enum_expr) ~ EOI }
constraint = { SOI ~ bool_expr ~ EOI }
"#]
pub struct DSLParser;

// ────────────────────────────────────────────────────────────────────────────────
//  AST definitions
// ────────────────────────────────────────────────────────────────────────────────

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum Leaf {
    Var(String, String),
    Global(String),
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum IntAtom {
    IntLit(i64),
    Leaf(Leaf),
    Expr(Box<IntExpr>),
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum IntExpr {
    Atom(IntAtom),
    Add(Box<IntExpr>, Box<IntExpr>),
    Sub(Box<IntExpr>, Box<IntExpr>),
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum EnumExpr {
    Leaf(Leaf),
    Variant {
        enum_name: Option<String>, // Explicit name if written FOO::A, else None
        variant: String,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum Expr {
    Int(IntExpr),
    Enum(EnumExpr)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum CmpOp {
    Eq,
    Ne,
    Gt,
    Ge,
    Lt,
    Le,
}

impl FromStr for CmpOp {
    type Err = GrammarError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Ok(match s {
            "==" => CmpOp::Eq,
            "!=" => CmpOp::Ne,
            ">" => CmpOp::Gt,
            ">=" => CmpOp::Ge,
            "<" => CmpOp::Lt,
            "<=" => CmpOp::Le,
            _ => return Err(GrammarError::InvalidCmpOp(s.to_string())),
        })
    }
}

impl fmt::Display for CmpOp {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CmpOp::Eq => write!(f, "=="),
            CmpOp::Ne => write!(f, "!="),
            CmpOp::Gt => write!(f, ">"),
            CmpOp::Ge => write!(f, ">="),
            CmpOp::Lt => write!(f, "<"),
            CmpOp::Le => write!(f, "<="),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum BoolExpr {
    Atom(BoolAtom),
    And(Box<BoolExpr>, Box<BoolExpr>),
    Or(Box<BoolExpr>, Box<BoolExpr>),
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum BoolAtom {
    Cmp(IntExpr, CmpOp, IntExpr),
    EnumIs(EnumExpr, EnumExpr),
    Expr(Box<BoolExpr>),
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Assignment {
    pub target: Leaf,
    pub expr: Expr,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Constraint {
    pub expr: BoolExpr,
}

// ────────────────────────────────────────────────────────────────────────────────
//  Evaluation
// ────────────────────────────────────────────────────────────────────────────────

pub struct EvalCtx {
    pub vars: HashMap<Leaf, FieldAttr>,
    pub enums: HashMap<String, EnumDef>,
    pub instance: Rc<RefCell<SatInstance>>,
}

impl Expr {
    pub fn eval(&self, ctx: &EvalCtx) -> FieldAttr {
        match self {
            Expr::Int(e) => e.eval(ctx),
            Expr::Enum(e) => e.eval(ctx),
        }
    }
}

impl IntExpr {
    pub fn eval(&self, ctx: &EvalCtx) -> FieldAttr {
        match self {
            IntExpr::Atom(a) => a.eval(ctx),
            IntExpr::Add(l, r) => {
                let l = l.eval(ctx);
                let r = r.eval(ctx);
                println!("Adding {:?} and {:?}", l, r);
                l.add(&r, &mut ctx.instance.borrow_mut())
            },
            IntExpr::Sub(l, r) => {
                let l = l.eval(ctx);
                let r = r.eval(ctx);
                l.sub(&r, &mut ctx.instance.borrow_mut())
            },
        }
    }
}

impl IntAtom {
    pub fn eval(&self, ctx: &EvalCtx) -> FieldAttr {
        match self {
            IntAtom::IntLit(i) => FieldAttr::const_int(&mut ctx.instance.borrow_mut(), *i as u32),
            IntAtom::Leaf(l) => ctx.vars[l].clone(),
            IntAtom::Expr(e) => e.eval(ctx),
        }
    }
}

impl EnumExpr {
    pub fn eval(&self, ctx: &EvalCtx) -> FieldAttr {
        match self {
            EnumExpr::Leaf(l) => ctx.vars[l].clone(),
            EnumExpr::Variant { enum_name, variant } => {
                let enum_def = ctx.enums.get(enum_name.as_ref().unwrap()).unwrap();
                let variant_idx = enum_def.variants.iter().position(|v| v == variant).unwrap();
                FieldAttr::const_enum(&mut ctx.instance.borrow_mut(), enum_def.variants.clone(), variant_idx)
            },
        }
    }
}

impl BoolExpr {
    pub fn eval(&self, ctx: &EvalCtx) -> Var {
        match self {
            BoolExpr::Atom(a) => a.eval(ctx),
            BoolExpr::And(l, r) => {
                let l = l.eval(ctx);
                let r = r.eval(ctx);
                and_clause(&mut ctx.instance.borrow_mut(), &[l, r])
            },
            BoolExpr::Or(l, r) => {
                let l = l.eval(ctx);
                let r = r.eval(ctx);
                or_clause(&mut ctx.instance.borrow_mut(), &[l, r])
            },
        }
    }
}

impl BoolAtom {
    pub fn eval(&self, ctx: &EvalCtx) -> Var {
        match self {
            BoolAtom::Expr(e) => e.eval(ctx),
            BoolAtom::Cmp(l, op, r) => {
                let l = l.eval(ctx);
                let r = r.eval(ctx);

                match op {
                    CmpOp::Eq => l.eq(&r, &mut ctx.instance.borrow_mut()),
                    CmpOp::Ne => l.ne(&r, &mut ctx.instance.borrow_mut()),
                    CmpOp::Gt => l.gt(&r, &mut ctx.instance.borrow_mut()),
                    CmpOp::Ge => l.ge(&r, &mut ctx.instance.borrow_mut()),
                    CmpOp::Lt => l.lt(&r, &mut ctx.instance.borrow_mut()),
                    CmpOp::Le => l.le(&r, &mut ctx.instance.borrow_mut()),
                }
            },
            BoolAtom::EnumIs(e1, e2) => {
                let e1 = e1.eval(ctx);
                let e2 = e2.eval(ctx);
                e1.eq(&e2, &mut ctx.instance.borrow_mut())
            },
        }
    }
}

// ────────────────────────────────────────────────────────────────────────────────
//  Pretty printing
// ────────────────────────────────────────────────────────────────────────────────

impl IntAtom {
    fn pprint(&self) -> String {
        match self {
            IntAtom::IntLit(i) => format!("{}", i).green().to_string(),
            IntAtom::Leaf(l) => l.pprint(),
            IntAtom::Expr(e) => format!("({})", e.pprint()),
        }
    }
}

impl IntExpr {
    fn pprint(&self) -> String {
        match self {
            IntExpr::Atom(a) => a.pprint(),
            IntExpr::Add(l, r) => format!("({} + {})", l.pprint(), r.pprint()),
            IntExpr::Sub(l, r) => format!("({} - {})", l.pprint(), r.pprint()),
        }
    }
}

impl EnumExpr {
    fn pprint(&self) -> String {
        match self {
            EnumExpr::Leaf(l) => l.pprint(),
            EnumExpr::Variant { enum_name, variant } => {
                if let Some(enum_name) = enum_name {
                    format!("{}::{}", enum_name, variant)
                } else {
                    format!("?::{}", variant)
                }
            }
        }
    }
}

impl BoolExpr {
    fn pprint(&self) -> String {
        match self {
            BoolExpr::And(l, r) => format!("({} and {})", l.pprint(), r.pprint()),
            BoolExpr::Or(l, r) => format!("({} or {})", l.pprint(), r.pprint()),
            BoolExpr::Atom(a) => a.pprint(),
        }
    }
}

impl BoolAtom {
    fn pprint(&self) -> String {
        match self {
            BoolAtom::Cmp(l, op, r) => format!("({} {} {})", l.pprint(), op, r.pprint()),
            BoolAtom::EnumIs(e1, e2) => format!("({} is {})", e1.pprint(), e2.pprint()),
            BoolAtom::Expr(e) => e.pprint(),
        }
    }
}
impl Expr {
    fn pprint(&self) -> String {
        match self {
            Expr::Int(e) => e.pprint(),
            Expr::Enum(e) => e.pprint(),
        }
    }
}

impl Leaf {
    fn pprint(&self) -> String {
        match self {
            Leaf::Var(v, f) => format!("{}", v).blue().to_string() + "." + &format!("{}", f).purple().to_string(),
            Leaf::Global(g) => "$".yellow().to_string() + "." + &format!("{}", g).purple().to_string(),
        }
    }
}

impl Assignment {
    pub fn pprint(&self) -> String {
        format!("{} := {}", self.target.pprint(), self.expr.pprint())
    }
}

impl Constraint {
    pub fn pprint(&self) -> String {
        format!("{}", self.expr.pprint())
    }
}

// ────────────────────────────────────────────────────────────────────────────────
// Display implementations
// ────────────────────────────────────────────────────────────────────────────────

impl fmt::Display for IntAtom {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            IntAtom::IntLit(i) => write!(f, "{}", i),
            IntAtom::Leaf(l) => write!(f, "{}", l),
            IntAtom::Expr(e) => write!(f, "({})", e),
        }
    }
}

impl fmt::Display for IntExpr {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            IntExpr::Atom(a) => write!(f, "{}", a),
            IntExpr::Add(l, r) => write!(f, "({} + {})", l, r),
            IntExpr::Sub(l, r) => write!(f, "({} - {})", l, r),
        }
    }
}

impl fmt::Display for EnumExpr {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EnumExpr::Leaf(l) => write!(f, "{}", l),
            EnumExpr::Variant { enum_name, variant } => {
                if let Some(enum_name) = enum_name {
                    write!(f, "{}::{}", enum_name, variant)
                } else {
                    write!(f, "?::{}", variant)
                }
            },
        }
    }
}

impl fmt::Display for BoolExpr {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BoolExpr::And(l, r) => write!(f, "({} and {})", l, r),
            BoolExpr::Or(l, r) => write!(f, "({} or {})", l, r),
            BoolExpr::Atom(a) => write!(f, "{}", a),
        }
    }
}

impl fmt::Display for BoolAtom {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BoolAtom::Cmp(l, op, r) => write!(f, "({} {} {})", l, op, r),
            BoolAtom::EnumIs(e1, e2) => write!(f, "({} is {})", e1, e2),
            BoolAtom::Expr(e) => write!(f, "({})", e),
        }
    }
}

impl fmt::Display for Expr {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Expr::Int(e) => write!(f, "{}", e),
            Expr::Enum(e) => write!(f, "{}", e),
        }
    }
}

impl fmt::Display for Leaf {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Leaf::Var(v, field) => write!(f, "{}.{}", v, field),
            Leaf::Global(g) => write!(f, "$.{}", g),
        }
    }
}

impl fmt::Display for Assignment {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} := {}", self.target, self.expr)
    }
}

impl fmt::Display for Constraint {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.expr)
    }
}

// ────────────────────────────────────────────────────────────────────────────────
//  Typing environment
// ────────────────────────────────────────────────────────────────────────────────

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Ty {
    Int,
    Enum(String), // name of the enum definition
    EnumVariant(String), // name of the enum variant (if we don't know the enum name yet)
    Bool,
}

#[derive(Clone, Debug)]
pub struct EnumDef {
    pub name: String,
    pub variants: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct VarDef {
    pub name: String,
    pub fields: HashMap<String, Ty>,
}

#[derive(Clone, Debug)]
pub struct Context {
    pub vars: HashMap<String, VarDef>,   // variable name -> definition
    pub globals: HashMap<String, Ty>,    // global field name -> type
    pub enums: HashMap<String, EnumDef>, // enum type name -> definition
}

impl Context {
    pub fn lookup_field(&self, r: &Leaf) -> Option<&Ty> {
        match r {
            Leaf::Var(v, f) => self.vars.get(v).and_then(|v| v.fields.get(f)),
            Leaf::Global(g) => self.globals.get(g),
        }
    }

    pub fn lookup_global(&self, name: &str) -> Option<&Ty> {
        self.globals.get(name)
    }

    pub fn lookup_enum(&self, name: &str) -> Option<&EnumDef> {
        self.enums.get(name)
    }
}

// ────────────────────────────────────────────────────────────────────────────────
//  Error type
// ────────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum GrammarError {
    Pest(pest::error::Error<Rule>),
    UnknownVariable(String),
    UnknownField { var: String, field: String },
    UnknownGlobal(String),
    UnknownEnumVariant { enum_name: String, variant: String },
    EnumInferenceFailure(String),
    TypeMismatch { expected: Ty, found: Ty },
    InvalidCmpOp(String),
}

impl From<pest::error::Error<Rule>> for GrammarError {
    fn from(e: pest::error::Error<Rule>) -> Self {
        GrammarError::Pest(e)
    }
}

impl fmt::Display for GrammarError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            GrammarError::Pest(e) => write!(f, "syntax error: {e}"),
            GrammarError::UnknownVariable(v) => write!(f, "unknown variable `{v}`"),
            GrammarError::UnknownField { var, field } => {
                write!(f, "unknown field `{field}` on variable `{var}`")
            }
            GrammarError::UnknownGlobal(g) => write!(f, "unknown global field `{g}`"),
            GrammarError::UnknownEnumVariant { enum_name, variant } => {
                write!(f, "unknown variant `{variant}` for enum `{enum_name}`")
            }
            GrammarError::EnumInferenceFailure(v) => {
                write!(f, "could not infer enum type for bare variant `{v}`")
            }
            GrammarError::TypeMismatch { expected, found } => {
                write!(f, "type mismatch: expected {expected:?}, found {found:?}")
            }
            GrammarError::InvalidCmpOp(op) => {
                write!(f, "invalid comparison operator: `{op}`")
            }
        }
    }
}

impl std::error::Error for GrammarError {}

// ────────────────────────────────────────────────────────────────────────────────
//  Parsing helpers
// ────────────────────────────────────────────────────────────────────────────────

pub struct ParserWithCtx<'a> {
    ctx: &'a Context,
}

impl<'a> ParserWithCtx<'a> {
    pub fn new(ctx: &'a Context) -> Self {
        Self { ctx }
    }

    // Top‑level helpers --------------------------------------------------------

    pub fn parse_assignment(&self, input: &str) -> Result<Assignment, GrammarError> {
        pest::set_error_detail(true);
        let mut pairs = DSLParser::parse(Rule::assignment, input)?;
        let pair = pairs.next().unwrap();
        let assignment = self.build_assignment(pair)?;
        self.typecheck_assignment(&assignment)?;
        Ok(assignment)
    }

    pub fn parse_constraint(&self, input: &str) -> Result<Constraint, GrammarError> {
        pest::set_error_detail(true);
        let mut pairs = DSLParser::parse(Rule::constraint, input)?;
        let pair = pairs.next().unwrap();
        let constraint = self.build_constraint(pair)?;
        self.typecheck_constraint(&constraint)?;
        Ok(constraint)
    }

    // Builders ----------------------------------------------------------------

    fn build_assignment(&self, pair: Pair<Rule>) -> Result<Assignment, GrammarError> {
        let mut inner = pair.into_inner();

        let target_pair = inner.next().unwrap();
        let expr_pair = inner.next().unwrap();

        let target = match target_pair.as_rule() {
            Rule::leaf => self.build_leaf(target_pair)?,
            _ => unreachable!(),
        };

        let target_ty = self.infer_target_type(&target)?;

        // Build the expression
        let mut expr = match expr_pair.as_rule() {
            Rule::int_expr => Expr::Int(self.build_int_expr(expr_pair)?),
            Rule::enum_expr => Expr::Enum(self.build_enum_expr(expr_pair)?),
            _ => unreachable!(),
        };

        if let Expr::Enum(e) = &mut expr {
            match e {
                EnumExpr::Variant { enum_name, variant } => {
                    if enum_name.is_none() {
                        if let Ty::Enum(ty_enum_name) = target_ty {
                            // Infer the enum name from the target
                            if self.validate_variant(&ty_enum_name, variant).is_err() {
                                return Err(GrammarError::EnumInferenceFailure(variant.clone()));
                            }
                            *enum_name = Some(ty_enum_name.clone());
                        } else {
                            return Err(GrammarError::EnumInferenceFailure(variant.clone()));
                        }
                    }
                }
                _ => {}
            }
        }

        Ok(Assignment { target, expr })
    }

    fn build_constraint(&self, pair: Pair<Rule>) -> Result<Constraint, GrammarError> {
        let mut inner = pair.into_inner();
        let expr_pair = inner.next().unwrap();
        let expr = self.build_bool_expr(expr_pair)?;
        Ok(Constraint { expr })
    }

    // ---- integer ------------------------------------------------------------

    fn build_int_expr(&self, pair: Pair<Rule>) -> Result<IntExpr, GrammarError> {
        let mut iter = pair.into_inner();
        let first = self.build_int_atom(iter.next().unwrap())?;
        let mut expr = IntExpr::Atom(first);
        while let Some(op_pair) = iter.next() {
            let op = op_pair.as_str();
            let rhs = self.build_int_atom(iter.next().unwrap())?;
            expr = match op {
                "+" => IntExpr::Add(Box::new(expr), Box::new(IntExpr::Atom(rhs))),
                "-" => IntExpr::Sub(Box::new(expr), Box::new(IntExpr::Atom(rhs))),
                _ => unreachable!(),
            };
        }
        Ok(expr)
    }

    fn build_int_atom(&self, pair: Pair<Rule>) -> Result<IntAtom, GrammarError> {
        match pair.as_rule() {
            Rule::leaf => {
                Ok(IntAtom::Leaf(self.build_leaf(pair)?))
            }
            Rule::int_lit => {
                let v = pair.as_str().parse::<i64>().unwrap();
                Ok(IntAtom::IntLit(v))
            }
            Rule::int_expr => Ok(IntAtom::Expr(Box::new(self.build_int_expr(pair)?))),
            _ => unreachable!(),
        }
    }

    // ---- enum ---------------------------------------------------------------

    fn build_enum_expr(&self, pair: Pair<Rule>) -> Result<EnumExpr, GrammarError> {
        let mut inner = pair.into_inner();
        let expr = inner.next().unwrap();
        match expr.as_rule() {
            Rule::leaf => Ok(EnumExpr::Leaf(self.build_leaf(expr)?)),
            Rule::variant_qual => {
                let mut i = expr.into_inner();
                let enum_name = i.next().unwrap().as_str().to_string();
                let variant = i.next().unwrap().as_str().to_string();
                self.validate_variant(&enum_name, &variant)?;
                Ok(EnumExpr::Variant {
                    enum_name: Some(enum_name),
                    variant,
                })
            }
            Rule::variant_bare => {
                let v = expr.as_str().to_string();
                // validation deferred until we can infer enum type
                Ok(EnumExpr::Variant {
                    enum_name: None,
                    variant: v,
                })
            }
            _ => unreachable!(),
        }
    }

    // ---- bool ---------------------------------------------------------------

    fn build_bool_expr(&self, pair: Pair<Rule>) -> Result<BoolExpr, GrammarError> {
        let mut iter = pair.into_inner();
        let first = self.build_bool_atom(iter.next().unwrap())?;
        let mut expr = BoolExpr::Atom(first);
        while let Some(op_pair) = iter.next() {
            let op = op_pair.as_str();
            let rhs = self.build_bool_atom(iter.next().unwrap())?;
            expr = match op {
                "and" => BoolExpr::And(Box::new(expr), Box::new(BoolExpr::Atom(rhs))),
                "or" => BoolExpr::Or(Box::new(expr), Box::new(BoolExpr::Atom(rhs))),
                _ => unreachable!(),
            };
        }
        Ok(expr)
    }

    fn build_bool_atom(&self, pair: Pair<Rule>) -> Result<BoolAtom, GrammarError> {
        match pair.as_rule() {
            Rule::bool_expr_cmp => {
                let mut i = pair.into_inner();
                let lhs = self.build_int_expr(i.next().unwrap())?;
                let op = i.next().unwrap().as_str();
                let rhs = self.build_int_expr(i.next().unwrap())?;
                Ok(BoolAtom::Cmp(lhs, CmpOp::from_str(op).unwrap(), rhs))
            }
            Rule::bool_expr_enum_is => {
                let mut i = pair.into_inner();
                let mut lhs = self.build_enum_expr(i.next().unwrap())?;
                let mut rhs = self.build_enum_expr(i.next().unwrap())?;

                let lhs_ty = self.infer_enum_expr_type(&lhs)?;
                let rhs_ty = self.infer_enum_expr_type(&rhs)?;

                match (&lhs_ty, &rhs_ty) {
                    (Ty::EnumVariant(lhs_variant), Ty::EnumVariant(rhs_variant)) => {
                        // Type validation error
                        Err(GrammarError::EnumInferenceFailure(format!("{} is {}", lhs_variant, rhs_variant)))
                    }
                    (Ty::EnumVariant(_), Ty::Enum(rhs_enum_name)) => {
                        if let EnumExpr::Variant { enum_name, variant } = &mut lhs {
                            if self.validate_variant(rhs_enum_name, variant).is_err() {
                                return Err(GrammarError::EnumInferenceFailure(variant.clone()));
                            }
                            *enum_name = Some(rhs_enum_name.clone());
                        }
                        Ok(BoolAtom::EnumIs(lhs, rhs))
                    }
                    (Ty::Enum(lhs_enum_name), Ty::EnumVariant(_)) => {
                        if let EnumExpr::Variant { enum_name, variant } = &mut rhs {
                            if self.validate_variant(lhs_enum_name, variant).is_err() {
                                return Err(GrammarError::EnumInferenceFailure(variant.clone()));
                            }
                            *enum_name = Some(lhs_enum_name.clone());
                        }
                        Ok(BoolAtom::EnumIs(lhs, rhs))
                    }
                    (Ty::Enum(lhs_enum_name), Ty::Enum(rhs_enum_name)) => {
                        if lhs_enum_name != rhs_enum_name {
                            return Err(GrammarError::EnumInferenceFailure(format!("{} is {}", lhs_enum_name, rhs_enum_name)));
                        }
                        Ok(BoolAtom::EnumIs(lhs, rhs))
                    }
                    _ => unreachable!(),
                }
            }
            Rule::bool_expr => Ok(BoolAtom::Expr(Box::new(self.build_bool_expr(pair)?))),
            _ => unreachable!(),
        }
    }

    // ---- shared helpers ------------------------------------------------------

    fn build_leaf(&self, pair: Pair<Rule>) -> Result<Leaf, GrammarError> {
        let mut inner = pair.into_inner();
        let leaf = inner.next().unwrap();
        match leaf.as_rule() {
            Rule::var_field => {
                let mut i = leaf.into_inner();
                let var = i.next().unwrap().as_str().to_string();
                let field = i.next().unwrap().as_str().to_string();
                Ok(Leaf::Var(var, field))
            }
            Rule::global_field => {
                let mut i = leaf.into_inner();
                let name = i.next().unwrap().as_str().to_string();
                Ok(Leaf::Global(name))
            }
            _ => unreachable!(),
        }
    }

    fn validate_variant(&self, enum_name: &str, variant: &str) -> Result<(), GrammarError> {
        match self.ctx.lookup_enum(enum_name) {
            Some(ed) if ed.variants.contains(&variant.to_string()) => Ok(()),
            _ => Err(GrammarError::UnknownEnumVariant {
                enum_name: enum_name.to_string(),
                variant: variant.to_string(),
            }),
        }
    }

    // Type inference ----------------------------------------------------------

    pub fn typecheck_assignment(&self, a: &Assignment) -> Result<(), GrammarError> {
        let target_ty = self.infer_target_type(&a.target)?;
        let expr_ty = self.infer_expr_type(&a.expr)?;
        if target_ty != expr_ty {
            Err(GrammarError::TypeMismatch {
                expected: target_ty,
                found: expr_ty,
            })
        } else {
            Ok(())
        }
    }

    pub fn typecheck_constraint(&self, c: &Constraint) -> Result<(), GrammarError> {
        let expr_ty = self.infer_bool_expr_type(&c.expr)?;
        if expr_ty != Ty::Bool {
            Err(GrammarError::TypeMismatch {
                expected: Ty::Bool,
                found: expr_ty,
            })
        } else {
            Ok(())
        }
    }

    fn infer_target_type(&self, target: &Leaf) -> Result<Ty, GrammarError> {
        match target {
            Leaf::Var(v, f) => {
                let field_ty = self.ctx.lookup_field(target).ok_or_else(|| {
                    GrammarError::UnknownField { var: v.clone(), field: f.clone() }
                })?;
                Ok(field_ty.clone())
            }
            Leaf::Global(g) => {
                let global_ty = self.ctx.lookup_global(g).ok_or_else(|| {
                    GrammarError::UnknownGlobal(g.clone())
                })?;
                Ok(global_ty.clone())
            }
        }
    }

    fn infer_expr_type(&self, expr: &Expr) -> Result<Ty, GrammarError> {
        match expr {
            Expr::Int(e) => self.infer_int_expr_type(e),
            Expr::Enum(e) => self.infer_enum_expr_type(e),
        }
    }

    fn infer_int_expr_type(&self, expr: &IntExpr) -> Result<Ty, GrammarError> {
        match expr {
            IntExpr::Atom(a) => self.infer_int_atom_type(a),
            IntExpr::Add(l, r) => {
                let lt = self.infer_int_expr_type(l)?;
                let rt = self.infer_int_expr_type(r)?;
                if lt != rt {
                    Err(GrammarError::TypeMismatch {
                        expected: lt,
                        found: rt,
                    })
                } else {
                    Ok(lt)
                }
            }
            IntExpr::Sub(l, r) => {
                let lt = self.infer_int_expr_type(l)?;
                let rt = self.infer_int_expr_type(r)?;
                if lt != rt {
                    Err(GrammarError::TypeMismatch {
                        expected: lt,
                        found: rt,
                    })
                } else {
                    Ok(lt)
                }
            }
        }
    }

    fn infer_int_atom_type(&self, atom: &IntAtom) -> Result<Ty, GrammarError> {
        match atom {
            IntAtom::IntLit(_) => Ok(Ty::Int),
            IntAtom::Leaf(l) => self.infer_target_type(l),
            IntAtom::Expr(e) => self.infer_int_expr_type(e),
        }
    }

    fn infer_enum_expr_type(&self, expr: &EnumExpr) -> Result<Ty, GrammarError> {
        match expr {
            EnumExpr::Leaf(l) => self.infer_target_type(l),
            EnumExpr::Variant { enum_name, variant } => {
                if let Some(enum_name) = enum_name {
                    Ok(Ty::Enum(enum_name.clone()))
                } else {
                    Ok(Ty::EnumVariant(variant.clone()))
                }
            }
        }
    }

    fn infer_bool_expr_type(&self, expr: &BoolExpr) -> Result<Ty, GrammarError> {
        match expr {
            BoolExpr::Atom(a) => self.infer_bool_atom_type(a),
            BoolExpr::And(l, r) => {
                let lt = self.infer_bool_expr_type(l)?;
                let rt = self.infer_bool_expr_type(r)?;
                if lt != rt {
                    Err(GrammarError::TypeMismatch {
                        expected: lt,
                        found: rt,
                    })
                } else {
                    Ok(lt)
                }
            }
            BoolExpr::Or(l, r) => {
                let lt = self.infer_bool_expr_type(l)?;
                let rt = self.infer_bool_expr_type(r)?;
                if lt != rt {
                    Err(GrammarError::TypeMismatch {
                        expected: lt,
                        found: rt,
                    })
                } else {
                    Ok(lt)
                }
            }
        }
    }

    fn infer_bool_atom_type(&self, atom: &BoolAtom) -> Result<Ty, GrammarError> {
        match atom {
            BoolAtom::Cmp(l, _, r) => {
                let lt = self.infer_int_expr_type(l)?;
                let rt = self.infer_int_expr_type(r)?;
                if lt != rt {
                    Err(GrammarError::TypeMismatch {
                        expected: lt,
                        found: rt,
                    })
                } else {
                    Ok(Ty::Bool)
                }
            }
            BoolAtom::EnumIs(e1, e2) => {
                let et1 = self.infer_enum_expr_type(e1)?;
                let et2 = self.infer_enum_expr_type(e2)?;
                if et1 != et2 {
                    Err(GrammarError::TypeMismatch {
                        expected: et1,
                        found: et2,
                    })
                } else {
                    Ok(Ty::Bool)
                }
            }
            BoolAtom::Expr(e) => self.infer_bool_expr_type(e),
        }
    }
    
}

// ────────────────────────────────────────────────────────────────────────────────
//  Tests
// ────────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_ctx() -> Context {
        let mut enums = HashMap::new();
        enums.insert(
            "FOO".into(),
            EnumDef {
                name: "FOO".into(),
                variants: ["A", "B", "C"].iter().map(|s| s.to_string()).collect(),
            },
        );
        enums.insert(
            "BAR".into(),
            EnumDef {
                name: "BAR".into(),
                variants: ["X", "Y"].iter().map(|s| s.to_string()).collect(),
            },
        );

        let var_a = VarDef {
            name: "foo".into(),
            fields: HashMap::from([
                ("x".into(), Ty::Int),
                ("tag".into(), Ty::Enum("FOO".into())),
            ]),
        };

        Context {
            vars: HashMap::from([(var_a.name.clone(), var_a)]),
            globals: HashMap::from([
                ("counter".into(), Ty::Int),
                ("mode".into(), Ty::Enum("BAR".into())),
            ]),
            enums,
        }
    }

    #[test]
    fn test_good_assignments() {
        let ctx = sample_ctx();
        let p = ParserWithCtx::new(&ctx);

        let good_assignments = vec![
            "foo.x := 1", // literal assignment
            "foo.x := $.counter", // global assignment
            "foo.x := 1 + 2", // arithmetic
            "foo.x := $.counter + $.counter", // arithmetic with globals
            "foo.x := 3 - (1 + $.counter)",
            "foo.x := (1 + 2) + (foo.x + 3)",
            "$.counter := foo.x - 100",
            "foo.tag := A",
            "foo.tag := FOO::A",
            "foo.tag := foo.tag",
        ];

        for b in good_assignments {
            let res = p.parse_assignment(b).unwrap();
            println!("{}", res.pprint());
        }
    }

    #[test]
    fn test_bad_assignments_syntax() {
        let ctx = sample_ctx();
        let p = ParserWithCtx::new(&ctx);

        let bad_assignments = vec![
            "asdf",
            "foo.x :=",
            "foo.x := a :=",
            "asdf := 1",
        ];

        for b in bad_assignments {
            let res = p.parse_assignment(b).unwrap_err();
            println!("{}", res);
        }
    }

    #[test]
    fn test_bad_assignments_type() {
        let ctx = sample_ctx();
        let p = ParserWithCtx::new(&ctx);

        let bad_assignments = vec![
            "foo.x := A", // Wrong type
            "foo.tag := X", // Wrong enum variant
            "$.counter := foo.tag", // Wrong type
            "foo.x := (1 + 2 + $.mode)", // wrong type
        ];

        for b in bad_assignments {
            let res = p.parse_assignment(b).unwrap_err();
            println!("{}", res);
        }
    }

    #[test]
    fn test_good_constraints() {
        let ctx = sample_ctx();
        let p = ParserWithCtx::new(&ctx);

        let good_constraints = vec![
            "foo.x == 1",
            "foo.x == $.counter",
            "foo.x == 1 + 2",
            "foo.x == $.counter + $.counter",
            "foo.x == 3 - (1 + $.counter)",
            "foo.x == 1 and foo.x == 2",
            "foo.x == 1 or foo.x == 2",
            "foo.x == 1 and (foo.x == 2 or foo.x == 3)",
            "foo.tag is A",
            "foo.tag is FOO::A",
            "foo.tag is foo.tag and foo.tag is FOO::A and foo.x >= 100",
        ];

        for c in good_constraints {
            let res = p.parse_constraint(c).unwrap();
            println!("{}", res.pprint());
        }
    }

    #[test]
    fn test_bad_constraints() {
        let ctx = sample_ctx();
        let p = ParserWithCtx::new(&ctx);

        let bad_constraints = vec![
            "foo.x == A", // Wrong type
            "foo.tag is X", // Wrong enum variant
        ];

        for c in bad_constraints {
            let res = p.parse_constraint(c).unwrap_err();
            println!("{}", res);
        }
    }
}
