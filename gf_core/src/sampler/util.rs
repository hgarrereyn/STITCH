use rustsat::{instances::SatInstance, types::{Assignment, Clause, TernaryVal, Var}};


pub fn get_idx(assignment: &Assignment, values: &[Var]) -> Option<usize> {
    let total = values.iter().map(|v| assignment.var_value(*v)).collect::<Vec<_>>();
    assert!(total.iter().filter(|v| **v == TernaryVal::True).count() <= 1);
    values.iter().position(|v| assignment.var_value(*v) == TernaryVal::True)
}

// x == (a ∧ b ∧ c ∧ ...)
pub fn and_clause(instance: &mut SatInstance, vars: &[Var]) -> Var {
    let x = instance.new_var();
    let mut cls = Clause::new();
    cls.add(x.pos_lit());
    for var in vars {
        cls.add(var.neg_lit());
        instance.add_binary(var.pos_lit(), x.neg_lit());
    }
    instance.add_clause(cls);
    x
}

// x == (a ∨ b ∨ c ∨ ...)
pub fn or_clause(instance: &mut SatInstance, vars: &[Var]) -> Var {
    let x = instance.new_var();
    let mut cls = Clause::new();
    cls.add(x.neg_lit());
    for var in vars {
        cls.add(var.pos_lit());
        instance.add_binary(var.neg_lit(), x.pos_lit());
    }
    instance.add_clause(cls);
    x
}

// x == (a == b)
pub fn eq_clause(instance: &mut SatInstance, a: &Var, b: &Var) -> Var {
    // (¬x ∨ ¬a ∨ b)
    // (¬x ∨ a ∨ ¬b)
    // (a ∨ b ∨ x)
    // (¬a ∨ ¬b ∨ x)
    let x = instance.new_var();
    instance.add_ternary(x.neg_lit(), a.neg_lit(), b.pos_lit());
    instance.add_ternary(x.neg_lit(), a.pos_lit(), b.neg_lit());
    instance.add_ternary(a.pos_lit(), b.pos_lit(), x.pos_lit());
    instance.add_ternary(a.neg_lit(), b.neg_lit(), x.pos_lit());
    x
}

pub fn ite_clause(instance: &mut SatInstance, cond: &Var, a: &Var, b: &Var) -> Var {
    // (¬x ∨ ¬c ∨ a)
    // (¬x ∨ c ∨ b)
    // (¬c ∨ ¬a ∨ x)
    // (c ∨ ¬b ∨ x)
    let x = instance.new_var();
    instance.add_ternary(x.neg_lit(), cond.neg_lit(), a.pos_lit());
    instance.add_ternary(x.neg_lit(), cond.pos_lit(), b.pos_lit());
    instance.add_ternary(cond.neg_lit(), a.neg_lit(), x.pos_lit());
    instance.add_ternary(cond.pos_lit(), b.neg_lit(), x.pos_lit());
    x
}

// x == ¬a
pub fn not_clause(instance: &mut SatInstance, a: &Var) -> Var {
    let x = instance.new_var();
    instance.add_binary(a.neg_lit(), x.neg_lit());
    instance.add_binary(a.pos_lit(), x.pos_lit());
    x
}

// x == (a ^ b)
pub fn xor_clause(instance: &mut SatInstance, a: &Var, b: &Var) -> Var {
    // (¬x ∨ a ∨ b)
    // (¬x ∨ ¬a ∨ ¬b)
    // (x ∨ ¬a ∨ b)
    // (x ∨ a ∨ ¬b)
    let x = instance.new_var();
    instance.add_ternary(x.neg_lit(), a.pos_lit(), b.pos_lit());
    instance.add_ternary(x.neg_lit(), a.neg_lit(), b.neg_lit());
    instance.add_ternary(x.pos_lit(), a.neg_lit(), b.pos_lit());
    instance.add_ternary(x.pos_lit(), a.pos_lit(), b.neg_lit());
    x
}
