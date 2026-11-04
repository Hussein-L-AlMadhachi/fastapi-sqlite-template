CREATE TABLE items_new (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    price REAL NOT NULL,
    tax REAL
);

INSERT INTO items_new (name, description, price, tax)
SELECT name, description, price, tax FROM items;

DROP TABLE items;
ALTER TABLE items_new RENAME TO items;
