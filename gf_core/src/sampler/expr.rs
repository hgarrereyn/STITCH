use rustsat::{instances::SatInstance, types::{constraints::CardConstraint, Assignment, Lit, TernaryVal, Var}};

use crate::pspec::{PAttr, PAttrType};

use super::util::{and_clause, eq_clause, get_idx, ite_clause, not_clause, or_clause, xor_clause};



#[derive(Clone, Debug)]
pub enum FieldAttr {
    Int(IntAttr),
    Enum(EnumAttr)
}

#[derive(Clone, Debug)]
pub struct IntAttr {
    pub bits: Vec<Var>
}

#[derive(Clone, Debug)]
pub struct EnumAttr {
    pub variants: Vec<String>,
    pub options: Vec<Var>
}

const INT_BITS: usize = 8;

impl FieldAttr {
    pub fn from_pattr(instance: &mut SatInstance, pattr: &PAttr) -> Self {
        match &pattr.typ {
            PAttrType::Int => {
                let mut bits = vec![];
                for i in 0..INT_BITS {
                    bits.push(instance.new_var());
                }
                Self::Int(IntAttr {
                    bits,
                })
            }
            PAttrType::Enum { variants } => {
                let mut options = vec![];
                for _ in variants {
                    options.push(instance.new_var());
                }
                instance.add_card_constr(CardConstraint::new_eq(options.iter().map(|v| v.pos_lit()).collect::<Vec<_>>(), 1));
                Self::Enum(EnumAttr {
                    variants: variants.iter().map(|v| v.to_string()).collect(),
                    options,
                })
            }
        }
    }

    pub fn decode(&self, assignment: &Assignment) -> String {
        match self {
            Self::Int(attr) => {
                attr.bits.iter()
                    .map(|bit| if assignment.var_value(*bit) == TernaryVal::True { 1 } else { 0 })
                    .rev()
                    .fold(0u32, |acc, bit| acc * 2 + bit)
                    .to_string()
            },
            Self::Enum(attr) => {
                let sel = get_idx(assignment, &attr.options);
                if let Some(idx) = sel {
                    attr.variants[idx].clone()
                } else {
                    "???".to_string()
                }
            }
        }
    }

    pub fn select(instance: &mut SatInstance, cond: &Var, a: &Self, b: &Self) -> Self {
        match (a, b) {
            (Self::Int(a), Self::Int(b)) => {
                let bits = a.bits.iter().zip(b.bits.iter())
                    .map(|(a, b)| ite_clause(instance, cond, a, b))
                    .collect::<Vec<_>>();

                Self::Int(IntAttr { bits })
            }
            (Self::Enum(a), Self::Enum(b)) => {
                let bits = a.options.iter().zip(b.options.iter())
                    .map(|(a, b)| ite_clause(instance, cond, a, b))
                    .collect::<Vec<_>>();

                Self::Enum(EnumAttr { variants: a.variants.clone(), options: bits })
            }
            _ => {
                panic!("Cannot select between different types");
            }
        }
    }

    pub fn add(&self, other: &Self, instance: &mut SatInstance) -> Self {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => Self::Int(a.add(b, instance)),
            _ => panic!("Cannot add different types"),
        }
    }

    pub fn sub(&self, other: &Self, instance: &mut SatInstance) -> Self {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => Self::Int(a.sub(b, instance)),
            _ => panic!("Cannot subtract different types"),
        }
    }

    pub fn eq(&self, other: &Self, instance: &mut SatInstance) -> Var {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => a.eq(b, instance),
            (Self::Enum(a), Self::Enum(b)) => a.eq(b, instance),
            _ => panic!("Cannot compare different types"),
        }
    }

    pub fn ne(&self, other: &Self, instance: &mut SatInstance) -> Var {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => a.ne(b, instance),
            _ => panic!("Cannot compare different types"),
        }
    }

    pub fn gt(&self, other: &Self, instance: &mut SatInstance) -> Var {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => a.gt(b, instance),
            _ => panic!("Cannot compare different types"),
        }
    }

    pub fn ge(&self, other: &Self, instance: &mut SatInstance) -> Var {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => a.ge(b, instance),
            _ => panic!("Cannot compare different types"),
        }
    }

    pub fn lt(&self, other: &Self, instance: &mut SatInstance) -> Var {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => a.lt(b, instance),
            _ => panic!("Cannot compare different types"),
        }
    }

    pub fn le(&self, other: &Self, instance: &mut SatInstance) -> Var {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => a.le(b, instance),
            _ => panic!("Cannot compare different types"),
        }
    }
    
    pub fn const_int(instance: &mut SatInstance, value: u32) -> Self {
        assert!(value < (1 << INT_BITS), "Value is too large to fit in {} bits", INT_BITS);
        let mut bits = vec![];
        for i in 0..INT_BITS {
            bits.push(instance.new_var());
            let val = (value >> i) & 1;
            if val == 1 {
                instance.add_unit(bits[i].pos_lit());
            } else {
                instance.add_unit(bits[i].neg_lit());
            }
        }
        Self::Int(IntAttr { bits })
    }

    pub fn const_enum(instance: &mut SatInstance, variants: Vec<String>, variant_idx: usize) -> Self {
        let mut options = vec![];

        for idx in 0..variants.len() {
            let var = instance.new_var();
            if idx == variant_idx {
                instance.add_unit(var.pos_lit());
            } else {
                instance.add_unit(var.neg_lit());
            }
            options.push(var);
        }
        Self::Enum(EnumAttr { variants, options })
    }
}


impl IntAttr {
    pub fn add(&self, other: &Self, instance: &mut SatInstance) -> Self {
        // Adder circuit

        let cin = instance.new_var();
        instance.add_unit(cin.neg_lit());

        // Add each bit of the two integers
        let mut carry = cin;
        let mut sum = vec![];
        
        for i in 0..INT_BITS {
            let a = self.bits[i];
            let b = other.bits[i];
            
            // s = ((a ^ b) ^ cin)
            let axb = xor_clause(instance, &a, &b);
            let s = xor_clause(instance, &axb, &carry);
            sum.push(s);

            // cout = (a & b) | ((a ^ b) & cin)
            let axb_and_cin = and_clause(instance, &[axb, carry]);
            let a_and_b = and_clause(instance, &[a, b]);
            let cout = or_clause(instance, &[a_and_b, axb_and_cin]);
            carry = cout;
        }

        Self { bits: sum }
    }

    pub fn sub(&self, other: &Self, instance: &mut SatInstance) -> Self {
        // Subtraction using two's complement: a - b = a + (~b + 1)
        
        // Start with carry-in set to 1 (adding 1 for two's complement)
        let cin = instance.new_var();
        instance.add_unit(cin.pos_lit());

        // Add each bit of self to the inverted bit of other
        let mut carry = cin;
        let mut result = vec![];
        
        for i in 0..INT_BITS {
            let a = self.bits[i];
            // Invert b (first part of two's complement)
            let not_b = not_clause(instance, &other.bits[i]);
            
            // s = ((a ^ ~b) ^ cin)
            let a_xor_notb = xor_clause(instance, &a, &not_b);
            let s = xor_clause(instance, &a_xor_notb, &carry);
            result.push(s);

            // cout = (a & ~b) | ((a ^ ~b) & cin)
            let a_xor_notb_and_cin = and_clause(instance, &[a_xor_notb, carry]);
            let a_and_notb = and_clause(instance, &[a, not_b]);
            let cout = or_clause(instance, &[a_and_notb, a_xor_notb_and_cin]);
            carry = cout;
        }

        Self { bits: result }
    }

    pub fn eq(&self, other: &Self, instance: &mut SatInstance) -> Var {
        let mut bits_eq = vec![];
        for (a, b) in self.bits.iter().zip(other.bits.iter()) {
            bits_eq.push(eq_clause(instance, a, b));
        }
        and_clause(instance, &bits_eq)
    }

    pub fn ne(&self, other: &Self, instance: &mut SatInstance) -> Var {
        let eq = self.eq(other, instance);
        not_clause(instance, &eq)
    }

    pub fn gt(&self, other: &Self, instance: &mut SatInstance) -> Var {
        todo!()
    }

    pub fn ge(&self, other: &Self, instance: &mut SatInstance) -> Var {
        todo!()
    }

    pub fn lt(&self, other: &Self, instance: &mut SatInstance) -> Var {
        todo!()
    }

    pub fn le(&self, other: &Self, instance: &mut SatInstance) -> Var {
        todo!()
    }
}

impl EnumAttr {
    pub fn eq(&self, other: &Self, instance: &mut SatInstance) -> Var {
        let mut eq = vec![];
        for (a, b) in self.options.iter().zip(other.options.iter()) {
            eq.push(eq_clause(instance, a, b));
        }
        and_clause(instance, &eq)
    }
}
