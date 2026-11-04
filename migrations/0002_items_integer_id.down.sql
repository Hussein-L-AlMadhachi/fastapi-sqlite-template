DROP TABLE items;

CREATE TABLE items (
    id primary key,
    name text not null,
    description text not null,
    price text not null,
    tax text not null
);
